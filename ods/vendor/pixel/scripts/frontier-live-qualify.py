#!/usr/bin/env python3
"""Consent-bound orchestration for one fixed synthetic Frontier provider check."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows test host
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - Linux deployment host
    msvcrt = None


QUALIFICATION_RE = re.compile(r"^qualification-[0-9]{13}-[a-f0-9]{12}$")
JOB_RE = re.compile(r"^frontier-[0-9]{13}-[a-f0-9]{12}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
AUTHORIZATION_RE = re.compile(r"^liveauth-[0-9]{13}-[a-f0-9]{12}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MAX_INPUT_TOKENS = 12000
OUTPUT_TOKENS = 256
MAX_COST_MICROS = 1_000_000
MAX_AUTHORIZATION_HOURS = 24
BOUNDARY = (
    "Fixed synthetic public data only. One exact external approval is required before "
    "the isolated broker may make at most one provider call."
)
REASON = "Deployment-owned synthetic Frontier live qualification"


class QualificationError(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat().replace("+00:00", "Z")


def parse_time(value: Any, label: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise QualificationError(f"{label} must be a timezone-aware timestamp") from exc


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise QualificationError(f"duplicate JSON field denied: {key}")
        value[key] = child
    return value


def safe_read_json(path: Path, maximum: int, *, private: bool = False) -> Any:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise QualificationError(f"cannot safely read {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= maximum:
            raise QualificationError(f"unsafe or oversized file: {path}")
        if private and os.name != "nt":
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise QualificationError(f"private file must be owned by the current user with mode 0600: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise QualificationError(f"oversized file: {path}")
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise QualificationError(f"file changed while it was read: {path}")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(QualificationError(f"non-finite JSON denied: {value}")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"invalid JSON file: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise QualificationError(f"private state path is unsafe: {path}")
    if os.name != "nt":
        if info.st_uid != os.geteuid():
            raise QualificationError(f"private state path is not owned by the current user: {path}")
        path.chmod(0o700)


def atomic_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
        raise QualificationError(f"record parent is unsafe: {path.parent}")
    if os.name != "nt" and parent_info.st_uid != os.geteuid():
        raise QualificationError(f"record parent is not owned by the current user: {path.parent}")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise QualificationError(f"record already exists: {path.name}") from exc
        temporary.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def required_environment() -> dict[str, str]:
    names = (
        "PIXEL_FRONTIER_BROKER_USER", "PIXEL_FRONTIER_BROKER_INSTALL_DIR",
        "PIXEL_FRONTIER_BROKER_STATE_DIR", "PIXEL_FRONTIER_POLICY_PATH",
        "PIXEL_FRONTIER_REQUEST_DIR", "PIXEL_FRONTIER_RESULT_DIR",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise QualificationError(f"missing generated Frontier setting: {missing[0]}")
    if not USER_RE.fullmatch(values["PIXEL_FRONTIER_BROKER_USER"]):
        raise QualificationError("unsafe Frontier broker user")
    for name in names[1:]:
        if name == "PIXEL_FRONTIER_BROKER_INSTALL_DIR":
            continue
        path = Path(values[name])
        if not path.is_absolute() or path == Path(path.anchor):
            raise QualificationError(f"{name} must be an absolute non-root path")
    install = Path(values["PIXEL_FRONTIER_BROKER_INSTALL_DIR"])
    if not install.is_absolute() or install == Path(install.anchor):
        raise QualificationError("PIXEL_FRONTIER_BROKER_INSTALL_DIR must be an absolute non-root path")
    return values


def broker_command(environment: dict[str, str], *arguments: str, timeout: int = 60) -> dict[str, Any]:
    executable = Path(environment["PIXEL_FRONTIER_BROKER_INSTALL_DIR"]) / "broker.py"
    command = [
        "sudo", "-u", environment["PIXEL_FRONTIER_BROKER_USER"], str(executable),
        "--policy", environment["PIXEL_FRONTIER_POLICY_PATH"],
        "--state", environment["PIXEL_FRONTIER_BROKER_STATE_DIR"],
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise QualificationError(
            "broker outcome is unknown after a timeout; rerun the same exact confirm command to recover without duplicate spend"
        ) from exc
    except OSError as exc:
        raise QualificationError("cannot start the isolated Frontier broker") from exc
    if len(completed.stdout) > 2 * 1024 * 1024 or len(completed.stderr) > 256 * 1024:
        raise QualificationError("Frontier broker diagnostics exceeded the qualification limit")
    if completed.returncode != 0:
        raise QualificationError("Frontier broker rejected the qualification; inspect local service diagnostics")
    try:
        value = json.loads(completed.stdout.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError("Frontier broker returned invalid qualification evidence") from exc
    if not isinstance(value, dict):
        raise QualificationError("Frontier broker returned non-object qualification evidence")
    return value


def validate_authorization(value: Any, *, require_active: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "$schema", "schemaVersion", "authorizationId", "purpose", "issuedAt", "expiresAt",
        "authMode", "maxProviderCalls", "maxInputTokens", "maxOutputTokens",
        "maxEstimatedCostMicros", "acknowledgements",
    }:
        raise QualificationError("live authorization has missing or unknown fields")
    if value["$schema"] != "./schemas/frontier-live-authorization-v1.schema.json" or value["schemaVersion"] != 1:
        raise QualificationError("live authorization schema is invalid")
    if not AUTHORIZATION_RE.fullmatch(str(value["authorizationId"])) or value["purpose"] != "pixel-frontier-live-qualification":
        raise QualificationError("live authorization identity or purpose is invalid")
    issued = parse_time(value["issuedAt"], "issuedAt")
    expires = parse_time(value["expiresAt"], "expiresAt")
    current = now()
    if issued > current + timedelta(minutes=5) or expires <= issued or expires > issued + timedelta(hours=MAX_AUTHORIZATION_HOURS):
        raise QualificationError("live authorization time window is invalid or exceeds 24 hours")
    if require_active and expires <= current:
        raise QualificationError("live authorization has expired")
    if value["authMode"] not in {"chatgpt", "api-key"} or value["maxProviderCalls"] != 1:
        raise QualificationError("live authorization must allow exactly one supported provider call")
    if value["maxInputTokens"] != MAX_INPUT_TOKENS:
        raise QualificationError("live authorization input ceiling must be exactly 12000 tokens")
    if value["maxOutputTokens"] != OUTPUT_TOKENS:
        raise QualificationError("live authorization output ceiling must be exactly 256 tokens")
    acknowledgements = value["acknowledgements"]
    expected_keys = {
        "syntheticOnly", "providerUsageAuthorized", "oneCallOnly", "outputIsUntrusted",
        "chatgptPlanOrCreditsAuthorized", "apiPlatformBillingAuthorized",
    }
    if not isinstance(acknowledgements, dict) or set(acknowledgements) != expected_keys:
        raise QualificationError("live authorization acknowledgements are invalid")
    if any(type(child) is not bool for child in acknowledgements.values()):
        raise QualificationError("live authorization acknowledgements must be booleans")
    if not all(acknowledgements[key] for key in ("syntheticOnly", "providerUsageAuthorized", "oneCallOnly", "outputIsUntrusted")):
        raise QualificationError("every live qualification safety acknowledgement must be accepted")
    if value["authMode"] == "chatgpt":
        if value["maxEstimatedCostMicros"] is not None or not acknowledgements["chatgptPlanOrCreditsAuthorized"] or acknowledgements["apiPlatformBillingAuthorized"]:
            raise QualificationError("ChatGPT authorization must acknowledge plan/credits without claiming API billing")
    else:
        maximum = value["maxEstimatedCostMicros"]
        if type(maximum) is not int or not 1 <= maximum <= MAX_COST_MICROS:
            raise QualificationError("API authorization requires a 1..1000000 micro-dollar ceiling")
        if not acknowledgements["apiPlatformBillingAuthorized"] or acknowledgements["chatgptPlanOrCreditsAuthorized"]:
            raise QualificationError("API authorization must explicitly acknowledge separate Platform billing")
    return value


def load_authorization(path: Path, *, require_active: bool = True) -> tuple[dict[str, Any], str]:
    value = validate_authorization(safe_read_json(path, 16384, private=True), require_active=require_active)
    return value, digest(value)


def make_identifier(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000):013d}-{secrets.token_hex(6)}"


def synthetic_request(qualification_id: str, job_id: str, receipt_id: str, created_at: str) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "jobId": job_id,
        "kind": "plan_review",
        "createdAt": created_at,
        "requester": "pixel_qualification",
        "classification": "public",
        "dataCategories": ["structural"],
        "payload": {
            "objective": "Review Pixel's fixed synthetic live-qualification capsule.",
            "assumptions": ["All content in this capsule is synthetic and public."],
            "constraints": [
                "Do not request private context.",
                "Return only the required structured review.",
            ],
            "localFindings": ["The local safety path requires one deployment-owned provider check."],
            "acceptanceCriteria": ["Return valid structured output without tools or additional data."],
        },
        "maxOutputTokens": OUTPUT_TOKENS,
        "reason": REASON,
        "routing": {
            "schemaVersion": 1,
            "receiptId": receipt_id,
            "observedAt": created_at,
            "localAttemptCount": 1,
            "localOutcome": "policy-required-review",
            "reasonCodes": ["security-review"],
        },
        "boundary": BOUNDARY,
    }


def publish_request(directory: Path, job_id: str, request: dict[str, Any]) -> None:
    if not JOB_RE.fullmatch(job_id):
        raise QualificationError("unsafe Frontier job ID")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise QualificationError("Frontier request spool is unsafe")
    destination = directory / f"{job_id}.json"
    temporary = directory / f".{job_id}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(json.dumps(request, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as exc:
            raise QualificationError("Frontier qualification job already exists") from exc
        temporary.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def wait_for_result(path: Path, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            value = safe_read_json(path, 1024 * 1024)
            if isinstance(value, dict) and value.get("status") in {
                "awaiting-approval", "preview", "succeeded", "failed", "cancelled", "rejected",
                "local-only", "local-retry", "operator-context",
            }:
                return value
        time.sleep(0.1)
    raise QualificationError("Frontier broker did not prepare the synthetic plan before the timeout")


def state_root() -> Path:
    configured = os.environ.get("PIXEL_FRONTIER_LIVE_QUALIFICATION_DIR")
    root = Path(configured) if configured else Path(__file__).resolve().parents[1] / ".generated" / "frontier-live-qualification"
    if not root.is_absolute() or root == Path(root.anchor):
        raise QualificationError("qualification state must be an absolute non-root path")
    ensure_private_directory(root)
    ensure_private_directory(root / "claims")
    ensure_private_directory(root / "receipts")
    return root


@contextmanager
def operator_lock(root: Path):
    """Serialize prepare/confirm so one consent cannot fork local claims."""
    path = root / "operator.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise QualificationError("qualification operator lock is unsafe")
        if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise QualificationError("qualification operator lock ownership or permissions are invalid")
        if info.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - production host is Linux
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover
            raise QualificationError("qualification operator locking is unavailable")
        try:
            yield
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - production host is Linux
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


def confirmation_binding(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "purpose": "pixel-frontier-live-qualification-confirmation",
        "qualificationId": claim["qualificationId"],
        "authorizationHash": claim["authorizationHash"],
        "authorizationExpiresAt": claim["authorizationExpiresAt"],
        "jobId": claim["jobId"],
        "planHash": claim["planHash"],
        "capsuleHash": claim["capsuleHash"],
        "policyHash": claim["policyHash"],
        "maxProviderCalls": 1,
        "maxInputTokens": claim["maxInputTokens"],
        "maxOutputTokens": claim["maxOutputTokens"],
        "maxEstimatedCostMicros": claim["maxEstimatedCostMicros"],
    }


def validate_local_claim(
    value: Any,
    *,
    authorization: dict[str, Any] | None = None,
    authorization_hash: str | None = None,
) -> dict[str, Any]:
    required = {
        "schemaVersion", "qualificationId", "authorizationHash", "authorizationExpiresAt",
        "authMode", "billingBoundary", "jobId", "planHash", "capsuleHash", "policyHash",
        "maxProviderCalls", "maxInputTokens", "maxOutputTokens", "maxEstimatedCostMicros",
        "preparedAt",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise QualificationError("stored qualification claim has missing or unknown fields")
    if (
        value["schemaVersion"] != 1
        or not QUALIFICATION_RE.fullmatch(str(value["qualificationId"]))
        or not SHA256_RE.fullmatch(str(value["authorizationHash"]))
        or not JOB_RE.fullmatch(str(value["jobId"]))
        or any(not SHA256_RE.fullmatch(str(value[key])) for key in ("planHash", "capsuleHash", "policyHash"))
        or value["authMode"] not in {"chatgpt", "api-key"}
        or value["billingBoundary"] != (
            "chatgpt-plan-or-credits" if value["authMode"] == "chatgpt" else "platform-api"
        )
        or value["maxProviderCalls"] != 1
        or value["maxInputTokens"] != MAX_INPUT_TOKENS
        or value["maxOutputTokens"] != OUTPUT_TOKENS
    ):
        raise QualificationError("stored qualification claim binding is invalid")
    parse_time(value["authorizationExpiresAt"], "authorizationExpiresAt")
    parse_time(value["preparedAt"], "preparedAt")
    maximum_cost = value["maxEstimatedCostMicros"]
    if value["authMode"] == "chatgpt":
        if maximum_cost is not None:
            raise QualificationError("stored ChatGPT qualification claim crossed into API billing")
    elif type(maximum_cost) is not int or not 1 <= maximum_cost <= MAX_COST_MICROS:
        raise QualificationError("stored API qualification claim has an invalid cost ceiling")
    if authorization is not None:
        expected_hash = digest(authorization)
        if authorization_hash is not None and not secrets.compare_digest(expected_hash, authorization_hash):
            raise QualificationError("supplied authorization hash is invalid")
        if (
            value["authorizationHash"] != expected_hash
            or value["authorizationExpiresAt"] != authorization["expiresAt"]
            or value["authMode"] != authorization["authMode"]
            or value["maxProviderCalls"] != authorization["maxProviderCalls"]
            or value["maxInputTokens"] != authorization["maxInputTokens"]
            or value["maxOutputTokens"] != authorization["maxOutputTokens"]
            or value["maxEstimatedCostMicros"] != authorization["maxEstimatedCostMicros"]
        ):
            raise QualificationError("stored qualification claim does not match the supplied authorization")
    return value


def prepared_projection(claim: dict[str, Any], authorization_path: Path) -> dict[str, Any]:
    confirmation_hash = digest(confirmation_binding(claim))
    return {
        "schemaVersion": 1,
        "qualificationId": claim["qualificationId"],
        "status": "awaiting-confirmation",
        "authMode": claim["authMode"],
        "billingBoundary": claim["billingBoundary"],
        "syntheticOnly": True,
        "maxProviderCalls": 1,
        "jobId": claim["jobId"],
        "planHash": claim["planHash"],
        "confirmationHash": confirmation_hash,
        "inspectCommand": f"./pixel frontier-show {claim['jobId']}",
        "confirmCommand": (
            "./pixel frontier-live-qualify confirm "
            f"{claim['qualificationId']} {confirmation_hash} "
            f"--authorization {shlex.quote(str(authorization_path))} --transmit"
        ),
        "warning": "The confirm command authorizes one real provider transmission of the displayed fixed synthetic capsule.",
    }


def validate_preflight(value: dict[str, Any], authorization: dict[str, Any]) -> None:
    if set(value) != {
        "schemaVersion", "status", "providerAuthMode", "billingBoundary", "policyHash",
        "qualificationLimits", "privacy",
    } or value["schemaVersion"] != 1 or value["status"] != "ready" or not SHA256_RE.fullmatch(str(value["policyHash"])):
        raise QualificationError("Frontier broker preflight evidence is malformed")
    if value["providerAuthMode"] != authorization["authMode"]:
        raise QualificationError("live authorization does not match the active Frontier authentication mode")
    expected_billing = "chatgpt-plan-or-credits" if authorization["authMode"] == "chatgpt" else "platform-api"
    if value["billingBoundary"] != expected_billing:
        raise QualificationError("live authorization does not match the active Frontier billing boundary")
    limits = value["qualificationLimits"]
    if not isinstance(limits, dict) or set(limits) != {
        "maxProviderCalls", "maxInputTokens", "maxOutputTokens", "maxEstimatedCostMicros",
        "requiredMaxEstimatedCostMicros",
    } or limits["maxProviderCalls"] != 1:
        raise QualificationError("Frontier broker preflight limits are malformed")
    if authorization["maxInputTokens"] > limits["maxInputTokens"] or authorization["maxOutputTokens"] > limits["maxOutputTokens"]:
        raise QualificationError("live authorization exceeds the broker qualification token ceiling")
    if authorization["authMode"] == "api-key" and authorization["maxEstimatedCostMicros"] > limits["maxEstimatedCostMicros"]:
        raise QualificationError("live authorization exceeds the current Frontier cost capacity")
    if authorization["authMode"] == "api-key" and authorization["maxEstimatedCostMicros"] < limits["requiredMaxEstimatedCostMicros"]:
        raise QualificationError("live authorization is below the worst-case metered qualification estimate")


def validate_prepared_result(result: dict[str, Any], request: dict[str, Any], preflight: dict[str, Any], authorization: dict[str, Any]) -> None:
    if (
        result.get("jobId") != request["jobId"]
        or result.get("status") != "awaiting-approval"
        or result.get("taskClass") != "plan_review"
        or result.get("classification") != "public"
        or result.get("dataCategories") != ["structural"]
        or result.get("providerAuthMode") != authorization["authMode"]
        or result.get("providerInvoked") is not False
        or result.get("executionSource") != "none"
        or result.get("maxOutputTokens") != OUTPUT_TOKENS
        or not SHA256_RE.fullmatch(str(result.get("planHash", "")))
        or not SHA256_RE.fullmatch(str(result.get("capsuleHash", "")))
        or result.get("routingReceipt", {}).get("policyHash") != preflight["policyHash"]
        or result.get("routingReceipt", {}).get("decision") != "propose"
        or result.get("routingReceipt", {}).get("localAttempt") != request["routing"]
        or result.get("sanitizedPreview", {}).get("payload") != request["payload"]
    ):
        raise QualificationError("Frontier broker did not produce the exact fixed synthetic approval plan")
    estimated_input = result.get("estimatedInputTokens")
    if type(estimated_input) is not int or estimated_input > authorization["maxInputTokens"]:
        raise QualificationError("prepared Frontier plan exceeds the authorized input ceiling")
    cost = result.get("costEstimate")
    if not isinstance(cost, dict):
        raise QualificationError("prepared Frontier plan omitted its cost boundary")
    if authorization["authMode"] == "chatgpt":
        if cost.get("mode") != "subscription" or cost.get("estimatedAmountMicros") is not None:
            raise QualificationError("prepared ChatGPT plan crossed into API billing")
    elif (
        cost.get("mode") != "metered"
        or type(cost.get("estimatedAmountMicros")) is not int
        or cost["estimatedAmountMicros"] > authorization["maxEstimatedCostMicros"]
    ):
        raise QualificationError("prepared API plan exceeds the authorized metered cost ceiling")


def authorization_command(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    if not output.is_absolute() or output == Path(output.anchor) or output.exists():
        raise QualificationError("authorization output must be a new absolute non-root file")
    if not args.authorize_one_synthetic_provider_call:
        raise QualificationError("authorization requires --authorize-one-synthetic-provider-call")
    if args.auth_mode == "chatgpt":
        if not args.authorize_chatgpt_plan_or_credits or args.authorize_api_billing or args.max_estimated_cost_micros is not None:
            raise QualificationError("ChatGPT authorization requires only --authorize-chatgpt-plan-or-credits")
        maximum_cost = None
    else:
        if not args.authorize_api_billing or args.authorize_chatgpt_plan_or_credits:
            raise QualificationError("API authorization requires only --authorize-api-billing")
        maximum_cost = args.max_estimated_cost_micros
        if type(maximum_cost) is not int or not 1 <= maximum_cost <= MAX_COST_MICROS:
            raise QualificationError("API authorization requires --max-estimated-cost-micros 1..1000000")
    issued = now()
    value = {
        "$schema": "./schemas/frontier-live-authorization-v1.schema.json",
        "schemaVersion": 1,
        "authorizationId": make_identifier("liveauth"),
        "purpose": "pixel-frontier-live-qualification",
        "issuedAt": iso(issued),
        "expiresAt": iso(issued + timedelta(minutes=args.expires_minutes)),
        "authMode": args.auth_mode,
        "maxProviderCalls": 1,
        "maxInputTokens": MAX_INPUT_TOKENS,
        "maxOutputTokens": OUTPUT_TOKENS,
        "maxEstimatedCostMicros": maximum_cost,
        "acknowledgements": {
            "syntheticOnly": True,
            "providerUsageAuthorized": True,
            "oneCallOnly": True,
            "outputIsUntrusted": True,
            "chatgptPlanOrCreditsAuthorized": args.auth_mode == "chatgpt",
            "apiPlatformBillingAuthorized": args.auth_mode == "api-key",
        },
    }
    validate_authorization(value)
    atomic_json(output, value)
    return {
        "schemaVersion": 1,
        "status": "authorization-created",
        "authorizationId": value["authorizationId"],
        "authMode": value["authMode"],
        "expiresAt": value["expiresAt"],
        "maxProviderCalls": 1,
        "maxEstimatedCostMicros": maximum_cost,
        "path": str(output),
        "next": f"./pixel frontier-live-qualify prepare --authorization {shlex.quote(str(output))}",
    }


def prepare_command(args: argparse.Namespace) -> dict[str, Any]:
    authorization_path = args.authorization.resolve()
    authorization, authorization_hash = load_authorization(authorization_path)
    root = state_root()
    with operator_lock(root):
        return prepare_locked(args, authorization_path, authorization, authorization_hash, root)


def prepare_locked(
    args: argparse.Namespace,
    authorization_path: Path,
    authorization: dict[str, Any],
    authorization_hash: str,
    root: Path,
) -> dict[str, Any]:
    claim_path = root / "claims" / f"{authorization_hash}.json"
    if claim_path.exists():
        claim = validate_local_claim(
            safe_read_json(claim_path, 65536, private=True),
            authorization=authorization,
            authorization_hash=authorization_hash,
        )
        return prepared_projection(claim, authorization_path)
    environment = required_environment()
    preflight = broker_command(environment, "--qualification-preflight")
    validate_preflight(preflight, authorization)
    qualification_id = make_identifier("qualification")
    job_id = make_identifier("frontier")
    receipt_id = make_identifier("local")
    created_at = iso()
    request = synthetic_request(qualification_id, job_id, receipt_id, created_at)
    publish_request(Path(environment["PIXEL_FRONTIER_REQUEST_DIR"]), job_id, request)
    result = wait_for_result(Path(environment["PIXEL_FRONTIER_RESULT_DIR"]) / f"{job_id}.json", args.timeout_seconds)
    validate_prepared_result(result, request, preflight, authorization)
    claim = {
        "schemaVersion": 1,
        "qualificationId": qualification_id,
        "authorizationHash": authorization_hash,
        "authorizationExpiresAt": authorization["expiresAt"],
        "authMode": authorization["authMode"],
        "billingBoundary": preflight["billingBoundary"],
        "jobId": job_id,
        "planHash": result["planHash"],
        "capsuleHash": result["capsuleHash"],
        "policyHash": preflight["policyHash"],
        "maxProviderCalls": 1,
        "maxInputTokens": authorization["maxInputTokens"],
        "maxOutputTokens": authorization["maxOutputTokens"],
        "maxEstimatedCostMicros": authorization["maxEstimatedCostMicros"],
        "preparedAt": iso(),
    }
    validate_local_claim(claim, authorization=authorization, authorization_hash=authorization_hash)
    atomic_json(claim_path, claim)
    return prepared_projection(claim, authorization_path)


def validate_receipt(
    receipt: dict[str, Any],
    qualification_id: str,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    auth_mode = authorization["authMode"]
    if set(receipt) != {
        "schemaVersion", "qualificationId", "status", "outcome", "checkedAt", "authMode",
        "billingBoundary", "syntheticOnly", "authorizationBound", "exactApprovalBound",
        "maxProviderCalls", "providerCallsObserved", "providerCallCeilingHeld", "usage", "cost", "privacy",
    }:
        raise QualificationError("broker qualification receipt has missing or unknown fields")
    if (
        receipt["schemaVersion"] != 1
        or receipt["qualificationId"] != qualification_id
        or receipt["authMode"] != auth_mode
        or receipt["status"] not in {"pass", "fail", "inconclusive"}
        or receipt["outcome"] not in {
            "provider-success", "provider-failed", "provider-cancelled", "provider-not-invoked",
            "interrupted-after-approval", "evidence-inconsistent",
        }
        or receipt["syntheticOnly"] is not True
        or receipt["authorizationBound"] is not True
        or receipt["exactApprovalBound"] is not True
        or receipt["maxProviderCalls"] != 1
        or type(receipt["providerCallsObserved"]) is not int
        or not 0 <= receipt["providerCallsObserved"] <= 200000
        or type(receipt["providerCallCeilingHeld"]) is not bool
        or receipt["providerCallCeilingHeld"] != (receipt["providerCallsObserved"] <= 1)
        or receipt["privacy"] != "Content-free qualification evidence; no prompt, response, credential, account, job, provider, or model identifier."
    ):
        raise QualificationError("broker qualification receipt is invalid")
    parse_time(receipt["checkedAt"], "checkedAt")
    expected_billing = "chatgpt-plan-or-credits" if auth_mode == "chatgpt" else "platform-api"
    if receipt["billingBoundary"] != expected_billing:
        raise QualificationError("broker qualification billing evidence is invalid")
    usage = receipt["usage"]
    if not isinstance(usage, dict) or set(usage) != {"available", "inputTokens", "outputTokens"} or type(usage["available"]) is not bool:
        raise QualificationError("broker qualification usage evidence is invalid")
    for key in ("inputTokens", "outputTokens"):
        if usage[key] is not None and (type(usage[key]) is not int or usage[key] < 0):
            raise QualificationError("broker qualification usage evidence is invalid")
    if not usage["available"] and (usage["inputTokens"] is not None or usage["outputTokens"] is not None):
        raise QualificationError("unavailable broker qualification usage must not claim token counts")
    if usage["available"] and any(type(usage[key]) is not int for key in ("inputTokens", "outputTokens")):
        raise QualificationError("available broker qualification usage requires exact token counts")
    if receipt["status"] == "pass" and (
        receipt["outcome"] != "provider-success"
        or receipt["providerCallsObserved"] != 1
        or not receipt["providerCallCeilingHeld"]
        or not usage["available"]
        or usage["inputTokens"] > authorization["maxInputTokens"]
        or usage["outputTokens"] > authorization["maxOutputTokens"]
    ):
        raise QualificationError("passing broker qualification evidence is internally inconsistent")
    if receipt["outcome"] == "provider-success" and receipt["status"] != "pass":
        raise QualificationError("provider-success qualification evidence must pass")
    if (receipt["status"] == "inconclusive") != (receipt["outcome"] == "interrupted-after-approval"):
        raise QualificationError("inconclusive qualification evidence must identify an interrupted exact approval")
    if receipt["status"] == "fail" and receipt["outcome"] not in {
        "provider-failed", "provider-cancelled", "provider-not-invoked", "evidence-inconsistent",
    }:
        raise QualificationError("failed qualification evidence has an invalid outcome")
    cost = receipt["cost"]
    if not isinstance(cost, dict) or set(cost) != {"mode", "currency", "estimatedAmountMicros"}:
        raise QualificationError("broker qualification cost evidence is invalid")
    amount = cost["estimatedAmountMicros"]
    if auth_mode == "chatgpt":
        if cost != {"mode": "subscription", "currency": None, "estimatedAmountMicros": None}:
            raise QualificationError("ChatGPT qualification receipt crossed into API billing")
    elif (
        cost["mode"] != "metered"
        or cost["currency"] != "USD"
        or (amount is not None and (type(amount) is not int or amount < 0))
        or (usage["available"] and type(amount) is not int)
        or (not usage["available"] and amount is not None)
        or (receipt["status"] == "pass" and amount > authorization["maxEstimatedCostMicros"])
    ):
        raise QualificationError("API qualification receipt has invalid or unauthorized billing evidence")
    return receipt


def confirm_command(args: argparse.Namespace) -> dict[str, Any]:
    if not args.transmit:
        raise QualificationError("confirm requires --transmit because it may make one real provider call")
    if not QUALIFICATION_RE.fullmatch(args.qualification_id) or not SHA256_RE.fullmatch(args.confirmation_hash):
        raise QualificationError("unsafe qualification ID or confirmation hash")
    authorization, authorization_hash = load_authorization(args.authorization.resolve(), require_active=False)
    root = state_root()
    with operator_lock(root):
        return confirm_locked(args, authorization, authorization_hash, root)


def confirm_locked(
    args: argparse.Namespace,
    authorization: dict[str, Any],
    authorization_hash: str,
    root: Path,
) -> dict[str, Any]:
    claim = validate_local_claim(
        safe_read_json(root / "claims" / f"{authorization_hash}.json", 65536, private=True),
        authorization=authorization,
        authorization_hash=authorization_hash,
    )
    if claim.get("qualificationId") != args.qualification_id or claim.get("authorizationHash") != authorization_hash:
        raise QualificationError("qualification does not match the supplied authorization")
    expected_confirmation = digest(confirmation_binding(claim))
    if not secrets.compare_digest(expected_confirmation, args.confirmation_hash):
        raise QualificationError("qualification confirmation hash mismatch")
    environment = required_environment()
    command = [
        "--qualification-approve", claim["jobId"],
        "--plan-hash", claim["planHash"],
        "--qualification-id", claim["qualificationId"],
        "--authorization-hash", authorization_hash,
        "--authorization-auth-mode", authorization["authMode"],
        "--authorization-expires-at", authorization["expiresAt"],
        "--authorization-max-input-tokens", str(authorization["maxInputTokens"]),
        "--authorization-max-output-tokens", str(authorization["maxOutputTokens"]),
    ]
    if authorization["maxEstimatedCostMicros"] is not None:
        command.extend(["--authorization-max-cost-micros", str(authorization["maxEstimatedCostMicros"])])
    receipt = validate_receipt(
        broker_command(environment, *command, timeout=1900),
        claim["qualificationId"],
        authorization,
    )
    local_receipt = root / "receipts" / f"{claim['qualificationId']}.json"
    if local_receipt.exists():
        if safe_read_json(local_receipt, 65536, private=True) != receipt:
            raise QualificationError("stored qualification receipt conflicts with broker evidence")
    else:
        atomic_json(local_receipt, receipt)
    return receipt


def show_command(args: argparse.Namespace) -> dict[str, Any]:
    if not QUALIFICATION_RE.fullmatch(args.qualification_id):
        raise QualificationError("unsafe qualification ID")
    root = state_root()
    claims = sorted((root / "claims").glob("*.json"))
    if len(claims) > 1000:
        raise QualificationError("qualification claim index exceeds the safe review limit")
    for path in claims:
        if not SHA256_RE.fullmatch(path.stem):
            continue
        claim = validate_local_claim(safe_read_json(path, 65536, private=True))
        if claim.get("qualificationId") == args.qualification_id:
            receipt_path = root / "receipts" / f"{args.qualification_id}.json"
            if receipt_path.exists():
                return validate_receipt(
                    safe_read_json(receipt_path, 65536, private=True),
                    args.qualification_id,
                    claim,
                )
            return {
                "schemaVersion": 1,
                "qualificationId": args.qualification_id,
                "status": "awaiting-confirmation",
                "authMode": claim["authMode"],
                "billingBoundary": claim["billingBoundary"],
                "syntheticOnly": True,
                "maxProviderCalls": 1,
                "jobId": claim["jobId"],
                "planHash": claim["planHash"],
                "confirmationHash": digest(confirmation_binding(claim)),
            }
    raise QualificationError("qualification was not found in local operator state")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare and explicitly approve one fixed synthetic Frontier live qualification."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorization = subparsers.add_parser("authorization", help="create one short-lived consent record; no provider call")
    authorization.add_argument("--auth-mode", choices=("chatgpt", "api-key"), required=True)
    authorization.add_argument("--output", type=Path, required=True)
    authorization.add_argument("--expires-minutes", type=int, choices=range(5, 61), default=30, metavar="5..60")
    authorization.add_argument("--max-estimated-cost-micros", type=int)
    authorization.add_argument("--authorize-one-synthetic-provider-call", action="store_true")
    authorization.add_argument("--authorize-chatgpt-plan-or-credits", action="store_true")
    authorization.add_argument("--authorize-api-billing", action="store_true")
    prepare = subparsers.add_parser("prepare", help="prepare an exact synthetic plan; no provider call")
    prepare.add_argument("--authorization", type=Path, required=True)
    prepare.add_argument("--timeout-seconds", type=int, choices=range(1, 121), default=30, metavar="1..120")
    confirm = subparsers.add_parser("confirm", help="approve the exact plan and make at most one provider call")
    confirm.add_argument("qualification_id")
    confirm.add_argument("confirmation_hash")
    confirm.add_argument("--authorization", type=Path, required=True)
    confirm.add_argument("--transmit", action="store_true")
    show = subparsers.add_parser("show", help="show content-free local qualification state")
    show.add_argument("qualification_id")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    handlers = {
        "authorization": authorization_command,
        "prepare": prepare_command,
        "confirm": confirm_command,
        "show": show_command,
    }
    print(json.dumps(handlers[args.command](args), indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        print(f"Pixel Frontier live qualification error: {error}", file=sys.stderr)
        raise SystemExit(2)
