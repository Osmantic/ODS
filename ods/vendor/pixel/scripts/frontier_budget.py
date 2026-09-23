#!/usr/bin/env python3
"""Create and apply exact, expiring custom Frontier budget proposals."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Callable


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PROPOSALS = 20
MAX_APPLICATIONS = 100
PROPOSAL_TTL_SECONDS = 900
PROPOSAL_RE = re.compile(r"^frontier-budget-[0-9]{13}-[a-f0-9]{12}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
BUDGET_FIELDS = (
    "windowSeconds", "maxJobs", "maxInputTokens", "maxOutputTokens",
    "maxFailures", "maxEstimatedCostMicros",
)
BUDGET_LIMITS = {
    "windowSeconds": (60, 2_678_400),
    "maxJobs": (1, 1_000),
    "maxInputTokens": (128, 10_000_000),
    "maxOutputTokens": (64, 2_000_000),
    "maxFailures": (1, 100),
    "maxEstimatedCostMicros": (0, 1_000_000_000),
}
POLICY_BUDGET_LIMITS = {
    "windowSeconds": (60, 2_678_400),
    "maxJobs": (1, 100_000),
    "maxInputTokens": (128, 1_000_000_000),
    "maxOutputTokens": (64, 1_000_000_000),
    "maxFailures": (1, 100_000),
    "maxEstimatedCostMicros": (0, 1_000_000_000_000),
}
APPROVAL_BOUNDARY = (
    "Draft only; applying this exact proposal requires its ID and SHA-256 hash plus "
    "--confirm in the trusted terminal. Application edits only the private source policy "
    "and does not configure, activate, restart, authenticate, approve, or call a provider."
)
APPLICATION_BOUNDARY = (
    "Private source policy edited only; no deployment, broker, authentication, approval, "
    "or provider call was activated."
)


class BudgetError(RuntimeError):
    pass


class BudgetInputError(BudgetError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any, label: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"{label} is invalid") from exc


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_private_bytes(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BudgetError("private budget input is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= maximum:
            raise BudgetError("private budget input is not a bounded single-link file")
        if os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise BudgetError("private budget input must be owner-bound mode 0600")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise BudgetError("private budget input is oversized")
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise BudgetError("private budget input changed during read")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_json(payload: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BudgetError(f"{label} contains duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                BudgetError(f"{label} contains a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BudgetError(f"{label} is not valid JSON") from exc
    return value


def private_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = read_private_bytes(path)
    value = parse_json(payload, label)
    if not isinstance(value, dict):
        raise BudgetError(f"{label} must be an object")
    return payload, value


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BudgetError("private budget directory is unsafe")
    if os.name != "nt" and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BudgetError("private budget directory must be owner-bound mode 0700")


def validate_private_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BudgetError("private budget directory is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise BudgetError("private budget directory is unsafe")
    if os.name != "nt" and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BudgetError("private budget directory must be owner-bound mode 0700")


@contextmanager
def budget_lock(state: Path):
    ensure_private_directory(state)
    lock_path = state / "frontier-budget.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BudgetError("private budget lock is unavailable or unsafe") from exc
    locked = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BudgetError("private budget lock is unsafe")
        if os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise BudgetError("private budget lock must be owner-bound mode 0600")
        if os.name == "nt":
            import msvcrt
            if info.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    except OSError as exc:
        raise BudgetError("private budget lock failed") from exc
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def bounded_record_names(path: Path, maximum: int, label: str) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if len(names) >= maximum:
                    raise BudgetError(f"private budget {label} retention limit is reached")
                if (
                    re.fullmatch(r"frontier-budget-[0-9]{13}-[a-f0-9]{12}\.json", entry.name) is None
                    or not entry.is_file(follow_symlinks=False)
                ):
                    raise BudgetError(f"private budget {label} directory contains an unsafe record")
                names.append(entry.name)
    except OSError as exc:
        raise BudgetError(f"private budget {label} directory is unavailable") from exc
    return names


def atomic_bytes(path: Path, payload: bytes, *, replace: bool) -> None:
    ensure_private_directory(path.parent)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except OSError as exc:
                raise BudgetError("private budget record already exists") from exc
            finally:
                temporary.unlink(missing_ok=True)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: Any, *, replace: bool) -> None:
    atomic_bytes(path, serialized_json(value), replace=replace)


def serialized_json(value: Any) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"


def load_broker(root: Path):
    source = root / "deploy" / "frontier-broker" / "broker.py"
    spec = importlib.util.spec_from_file_location("pixel_frontier_budget_broker", source)
    if spec is None or spec.loader is None:
        raise BudgetError("Frontier policy validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_policy(root: Path, value: Any) -> dict[str, Any]:
    try:
        policy = load_broker(root).validate_policy(value)
    except Exception as exc:
        raise BudgetError("private Frontier policy failed exact validation") from exc
    if policy.get("schemaVersion") != 2 or policy.get("provider", {}).get("kind") != "codex":
        raise BudgetError("custom budget editing requires a schema-v2 Codex policy")
    return policy


def validate_budget_request(value: Any, auth_mode: str, cost_mode: str) -> dict[str, int | None]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", *BUDGET_FIELDS} or value.get("schemaVersion") != 1:
        raise BudgetInputError("custom budget request has missing or unknown fields")
    result: dict[str, int | None] = {}
    for field in BUDGET_FIELDS:
        candidate = value.get(field)
        if field == "maxEstimatedCostMicros" and candidate is None:
            result[field] = None
            continue
        low, high = BUDGET_LIMITS[field]
        if type(candidate) is not int or not low <= candidate <= high:
            raise BudgetInputError(f"{field} is outside the safe editor range")
        result[field] = candidate
    if result["maxOutputTokens"] > result["maxInputTokens"]:
        raise BudgetInputError("maxOutputTokens cannot exceed maxInputTokens")
    if result["maxFailures"] > result["maxJobs"]:
        raise BudgetInputError("maxFailures cannot exceed maxJobs")
    if auth_mode == "chatgpt":
        if cost_mode != "subscription" or result["maxEstimatedCostMicros"] is not None:
            raise BudgetInputError("ChatGPT custom budgets use subscription mode without an API cost field")
    elif auth_mode == "api-key":
        if cost_mode != "metered" or result["maxEstimatedCostMicros"] is None:
            raise BudgetInputError("API-key custom budget editing requires a metered policy and explicit cost ceiling")
    else:
        raise BudgetError("private Frontier authentication mode is invalid")
    return result


def validate_recorded_budgets(value: Any, *, proposed: bool, auth_mode: str) -> dict[str, int | None]:
    if not isinstance(value, dict) or set(value) != set(BUDGET_FIELDS):
        raise BudgetError("private budget proposal values are invalid")
    limits = BUDGET_LIMITS if proposed else POLICY_BUDGET_LIMITS
    result: dict[str, int | None] = {}
    for field in BUDGET_FIELDS:
        candidate = value[field]
        if field == "maxEstimatedCostMicros" and candidate is None:
            result[field] = None
            continue
        low, high = limits[field]
        if type(candidate) is not int or not low <= candidate <= high:
            raise BudgetError("private budget proposal values are invalid")
        result[field] = candidate
    if result["maxOutputTokens"] > result["maxInputTokens"] or result["maxFailures"] > result["maxJobs"]:
        raise BudgetError("private budget proposal values are incoherent")
    if auth_mode == "chatgpt" and result["maxEstimatedCostMicros"] is not None:
        raise BudgetError("private ChatGPT budget proposal contains an API cost field")
    if proposed and auth_mode == "api-key" and result["maxEstimatedCostMicros"] is None:
        raise BudgetError("private API budget proposal has no explicit cost ceiling")
    return result


def policy_from_onboarding(root: Path, onboarding_path: Path) -> tuple[Path, bytes, dict[str, Any]]:
    _onboarding_bytes, onboarding = private_json(onboarding_path, "private onboarding")
    if onboarding.get("frontierLimbEnabled") is not True or onboarding.get("frontierBudgetProfile") != "custom":
        raise BudgetInputError("custom Frontier budgets are not enabled in private onboarding")
    policy_value = onboarding.get("frontierPolicyFile")
    if not isinstance(policy_value, str):
        raise BudgetError("private custom Frontier policy path is invalid")
    policy_path = Path(policy_value)
    if not policy_path.is_absolute() or policy_path == Path(policy_path.anchor):
        raise BudgetError("private custom Frontier policy path is invalid")
    try:
        if policy_path.resolve(strict=True) != policy_path:
            raise BudgetError("private custom Frontier policy path contains a link")
    except OSError as exc:
        raise BudgetError("private custom Frontier policy path is unavailable") from exc
    validate_private_directory(policy_path.parent)
    policy_bytes, raw_policy = private_json(policy_path, "private Frontier policy")
    policy = validate_policy(root, raw_policy)
    auth_mode = policy["provider"]["authMode"]
    if onboarding.get("frontierAuthMode") != auth_mode:
        raise BudgetError("private onboarding and Frontier policy authentication modes differ")
    return policy_path, policy_bytes, policy


def public_proposal(value: dict[str, Any]) -> dict[str, Any]:
    return dict(value["public"])


def validate_private_proposal(value: Any, *, expected_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "policyPath", "public"} or value.get("schemaVersion") != 1:
        raise BudgetError("private budget proposal envelope is invalid")
    policy_path = value.get("policyPath")
    public = value.get("public")
    required = {
        "schemaVersion", "proposalId", "createdAt", "expiresAt", "authMode",
        "billingBoundary", "policySha256", "currentBudgets", "proposedBudgets",
        "directions", "increasesPotentialSpend", "activation", "browserCanApply",
        "browserCanActivate", "approvalBoundary", "proposalHash",
    }
    if (
        not isinstance(policy_path, str) or not Path(policy_path).is_absolute()
        or not isinstance(public, dict) or set(public) != required or public.get("schemaVersion") != 1
    ):
        raise BudgetError("private budget proposal shape is invalid")
    proposal_id = public.get("proposalId")
    if not isinstance(proposal_id, str) or PROPOSAL_RE.fullmatch(proposal_id) is None or (expected_id and proposal_id != expected_id):
        raise BudgetError("private budget proposal identity is invalid")
    proposal_hash = public.get("proposalHash")
    unsigned = dict(public)
    unsigned.pop("proposalHash", None)
    bound = {"policyPath": policy_path, "proposal": unsigned}
    if not isinstance(proposal_hash, str) or not HASH_RE.fullmatch(proposal_hash) or digest(bound) != proposal_hash:
        raise BudgetError("private budget proposal hash is invalid")
    if public.get("approvalBoundary") != APPROVAL_BOUNDARY or public.get("browserCanApply") is not False or public.get("browserCanActivate") is not False:
        raise BudgetError("private budget proposal authority boundary is invalid")
    created = parse_time(public.get("createdAt"), "budget proposal creation")
    expires = parse_time(public.get("expiresAt"), "budget proposal expiry")
    if expires <= created or expires - created != timedelta(seconds=PROPOSAL_TTL_SECONDS):
        raise BudgetError("private budget proposal lifetime is invalid")
    if not isinstance(public.get("policySha256"), str) or not HASH_RE.fullmatch(public["policySha256"]):
        raise BudgetError("private budget policy binding is invalid")
    if public.get("authMode") not in {"chatgpt", "api-key"}:
        raise BudgetError("private budget proposal authentication mode is invalid")
    if public.get("billingBoundary") != ("chatgpt-plan" if public["authMode"] == "chatgpt" else "platform-api"):
        raise BudgetError("private budget proposal billing boundary is invalid")
    current = validate_recorded_budgets(
        public.get("currentBudgets"), proposed=False, auth_mode=public["authMode"],
    )
    proposed = validate_recorded_budgets(
        public.get("proposedBudgets"), proposed=True, auth_mode=public["authMode"],
    )
    directions = public.get("directions")
    if not isinstance(directions, dict) or set(directions) != set(BUDGET_FIELDS):
        raise BudgetError("private budget proposal directions are invalid")
    for field in BUDGET_FIELDS:
        old, new = current[field], proposed[field]
        expected = "same" if old == new else "increase" if old is None or (new is not None and new > old) else "decrease"
        if directions.get(field) != expected:
            raise BudgetError("private budget proposal direction is invalid")
    if all(direction == "same" for direction in directions.values()):
        raise BudgetError("private budget proposal makes no change")
    if public.get("activation") != "configure-plan-apply-required":
        raise BudgetError("private budget proposal activation boundary is invalid")
    expected_spend = any(
        directions[field] == "increase" for field in (
            "maxJobs", "maxInputTokens", "maxOutputTokens", "maxFailures",
            "maxEstimatedCostMicros",
        )
    ) or directions["windowSeconds"] == "decrease"
    if type(public.get("increasesPotentialSpend")) is not bool or public["increasesPotentialSpend"] != expected_spend:
        raise BudgetError("private budget proposal spend direction is invalid")
    return value


def validate_application(value: Any, proposal: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schemaVersion", "status", "proposalId", "proposalHash", "priorPolicySha256",
        "updatedPolicySha256", "budgets", "appliedAt", "backupCreated", "activated",
        "nextActionCode", "boundary",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("schemaVersion") != 1:
        raise BudgetError("private budget application record is invalid")
    if (
        value.get("proposalId") != proposal["proposalId"]
        or value.get("proposalHash") != proposal["proposalHash"]
        or value.get("priorPolicySha256") != proposal["policySha256"]
        or value.get("budgets") != proposal["proposedBudgets"]
        or not isinstance(value.get("updatedPolicySha256"), str)
        or HASH_RE.fullmatch(value["updatedPolicySha256"]) is None
        or value.get("activated") is not False
        or value.get("nextActionCode") != "configure-plan-apply"
        or value.get("boundary") != APPLICATION_BOUNDARY
    ):
        raise BudgetError("private budget application binding is invalid")
    if value.get("status") == "applying":
        if value.get("appliedAt") is not None or value.get("backupCreated") is not False:
            raise BudgetError("private budget application claim is invalid")
    elif value.get("status") == "applied":
        parse_time(value.get("appliedAt"), "budget application time")
        if value.get("backupCreated") is not True:
            raise BudgetError("private budget application receipt is invalid")
    else:
        raise BudgetError("private budget application status is invalid")
    return value


def create_proposal(
    root: Path, state: Path, onboarding_path: Path, request: Any,
    *, now: Callable[[], datetime] = utcnow,
) -> dict[str, Any]:
    with budget_lock(state):
        return _create_proposal(root, state, onboarding_path, request, now=now)


def _create_proposal(
    root: Path, state: Path, onboarding_path: Path, request: Any,
    *, now: Callable[[], datetime],
) -> dict[str, Any]:
    policy_path, policy_bytes, policy = policy_from_onboarding(root, onboarding_path)
    auth_mode = policy["provider"]["authMode"]
    cost_mode = policy["provider"]["cost"]["mode"]
    proposed = validate_budget_request(request, auth_mode, cost_mode)
    current = {field: policy["budgets"][field] for field in BUDGET_FIELDS}
    directions = {
        field: "same" if current[field] == proposed[field]
        else "increase" if current[field] is None or (proposed[field] is not None and proposed[field] > current[field])
        else "decrease"
        for field in BUDGET_FIELDS
    }
    if all(direction == "same" for direction in directions.values()):
        raise BudgetInputError("custom budget proposal must change at least one limit")
    created = now().astimezone(timezone.utc)
    proposal_id = f"frontier-budget-{int(created.timestamp() * 1000):013d}-{secrets.token_hex(6)}"
    public = {
        "schemaVersion": 1,
        "proposalId": proposal_id,
        "createdAt": iso(created),
        "expiresAt": iso(created + timedelta(seconds=PROPOSAL_TTL_SECONDS)),
        "authMode": auth_mode,
        "billingBoundary": "chatgpt-plan" if auth_mode == "chatgpt" else "platform-api",
        "policySha256": hashlib.sha256(policy_bytes).hexdigest(),
        "currentBudgets": current,
        "proposedBudgets": proposed,
        "directions": directions,
        "increasesPotentialSpend": any(
            directions[field] == "increase" for field in (
                "maxJobs", "maxInputTokens", "maxOutputTokens", "maxFailures",
                "maxEstimatedCostMicros",
            )
        ) or directions["windowSeconds"] == "decrease",
        "activation": "configure-plan-apply-required",
        "browserCanApply": False,
        "browserCanActivate": False,
        "approvalBoundary": APPROVAL_BOUNDARY,
    }
    public["proposalHash"] = digest({"policyPath": str(policy_path), "proposal": public})
    envelope = {"schemaVersion": 1, "policyPath": str(policy_path), "public": public}
    validate_private_proposal(envelope, expected_id=proposal_id)
    proposals = state / "frontier-budget-proposals"
    applications = state / "frontier-budget-applications"
    ensure_private_directory(proposals)
    ensure_private_directory(applications)
    application_names = set(bounded_record_names(applications, MAX_APPLICATIONS, "application"))
    proposal_names = bounded_record_names(proposals, MAX_PROPOSALS, "proposal")
    for name in proposal_names:
        path = proposals / name
        _payload, candidate = private_json(path, "private budget proposal")
        candidate = validate_private_proposal(candidate, expected_id=name[:-5])
        if parse_time(candidate["public"]["expiresAt"], "budget proposal expiry") <= created:
            application_name = name
            if application_name in application_names:
                _application_bytes, application = private_json(
                    applications / application_name, "private budget application",
                )
                application = validate_application(application, candidate["public"])
                if application["status"] == "applying":
                    continue
            try:
                path.unlink()
            except OSError as exc:
                raise BudgetError("expired private budget proposal could not be removed") from exc
    if len(bounded_record_names(proposals, MAX_PROPOSALS, "proposal")) >= MAX_PROPOSALS:
        raise BudgetError("private budget proposal retention limit is reached")
    atomic_json(proposals / f"{proposal_id}.json", envelope, replace=False)
    return public


def apply_proposal(
    root: Path, state: Path, onboarding_path: Path, proposal_id: str, proposal_hash: str,
    *, now: Callable[[], datetime] = utcnow,
) -> dict[str, Any]:
    with budget_lock(state):
        return _apply_proposal(
            root, state, onboarding_path, proposal_id, proposal_hash, now=now,
        )


def _apply_proposal(
    root: Path, state: Path, onboarding_path: Path, proposal_id: str, proposal_hash: str,
    *, now: Callable[[], datetime],
) -> dict[str, Any]:
    if PROPOSAL_RE.fullmatch(proposal_id) is None or HASH_RE.fullmatch(proposal_hash) is None:
        raise BudgetInputError("budget proposal ID or hash is invalid")
    proposal_path = state / "frontier-budget-proposals" / f"{proposal_id}.json"
    _proposal_bytes, envelope = private_json(proposal_path, "private budget proposal")
    proposal = validate_private_proposal(envelope, expected_id=proposal_id)["public"]
    if not secrets.compare_digest(proposal["proposalHash"], proposal_hash):
        raise BudgetInputError("budget proposal hash does not match")
    applications = state / "frontier-budget-applications"
    ensure_private_directory(applications)
    application_names = bounded_record_names(applications, MAX_APPLICATIONS, "application")
    application_path = applications / f"{proposal_id}.json"
    existing_application = None
    if application_path.name in application_names:
        _application_bytes, existing_application = private_json(
            application_path, "private budget application",
        )
        existing_application = validate_application(existing_application, proposal)
    elif len(application_names) >= MAX_APPLICATIONS:
        raise BudgetError("private budget application retention limit is reached")
    current_time = now().astimezone(timezone.utc)
    if (
        existing_application is None
        and current_time >= parse_time(proposal["expiresAt"], "budget proposal expiry")
    ):
        raise BudgetInputError("budget proposal expired; create a new exact proposal")
    policy_path, policy_bytes, policy = policy_from_onboarding(root, onboarding_path)
    if str(policy_path) != envelope["policyPath"]:
        raise BudgetInputError("private onboarding policy path changed after proposal creation")
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    prior_policy = secrets.compare_digest(policy_sha256, proposal["policySha256"])
    if policy["provider"]["authMode"] != proposal["authMode"]:
        raise BudgetError("private Frontier authentication changed after proposal creation")
    if existing_application is not None and existing_application["status"] == "applied":
        if not secrets.compare_digest(policy_sha256, existing_application["updatedPolicySha256"]):
            raise BudgetError("applied budget receipt no longer matches the private policy")
        raise BudgetInputError("budget proposal was already applied")
    if prior_policy:
        if policy["budgets"] != proposal["currentBudgets"]:
            raise BudgetError("private Frontier policy no longer matches the proposal")
    elif existing_application is None or existing_application["status"] != "applying":
        raise BudgetInputError("private Frontier policy changed after proposal creation")
    elif policy["budgets"] != proposal["proposedBudgets"]:
        raise BudgetError("claimed budget application has unrecognized policy state")
    validate_budget_request({"schemaVersion": 1, **proposal["proposedBudgets"]}, policy["provider"]["authMode"], policy["provider"]["cost"]["mode"])
    updated = dict(policy)
    updated["budgets"] = dict(proposal["proposedBudgets"])
    updated = validate_policy(root, updated)
    updated_bytes = serialized_json(updated)
    updated_sha256 = hashlib.sha256(updated_bytes).hexdigest()
    if existing_application is not None:
        if not secrets.compare_digest(existing_application["updatedPolicySha256"], updated_sha256):
            raise BudgetError("claimed budget application does not match the exact updated policy")
        if not prior_policy and not secrets.compare_digest(policy_sha256, updated_sha256):
            raise BudgetError("claimed budget application has unrecognized policy bytes")
    else:
        existing_application = {
            "schemaVersion": 1,
            "status": "applying",
            "proposalId": proposal_id,
            "proposalHash": proposal_hash,
            "priorPolicySha256": proposal["policySha256"],
            "updatedPolicySha256": updated_sha256,
            "budgets": dict(proposal["proposedBudgets"]),
            "appliedAt": None,
            "backupCreated": False,
            "activated": False,
            "nextActionCode": "configure-plan-apply",
            "boundary": APPLICATION_BOUNDARY,
        }
        validate_application(existing_application, proposal)
        atomic_json(application_path, existing_application, replace=False)
    backup = policy_path.parent / f"{policy_path.name}.before-{proposal_id}.json"
    if backup.exists() or backup.is_symlink():
        backup_bytes = read_private_bytes(backup)
        if not secrets.compare_digest(hashlib.sha256(backup_bytes).hexdigest(), proposal["policySha256"]):
            raise BudgetError("exact private budget backup is invalid")
    elif prior_policy:
        atomic_bytes(backup, policy_bytes, replace=False)
    else:
        raise BudgetError("claimed budget application has no exact private backup")
    if prior_policy:
        atomic_bytes(policy_path, updated_bytes, replace=True)
    if not secrets.compare_digest(hashlib.sha256(read_private_bytes(policy_path)).hexdigest(), updated_sha256):
        raise BudgetError("updated private Frontier policy could not be verified")
    receipt = {
        "schemaVersion": 1,
        "status": "applied",
        "proposalId": proposal_id,
        "proposalHash": proposal_hash,
        "priorPolicySha256": proposal["policySha256"],
        "updatedPolicySha256": updated_sha256,
        "budgets": dict(proposal["proposedBudgets"]),
        "appliedAt": iso(current_time),
        "backupCreated": True,
        "activated": False,
        "nextActionCode": "configure-plan-apply",
        "boundary": APPLICATION_BOUNDARY,
    }
    validate_application(receipt, proposal)
    atomic_json(application_path, receipt, replace=True)
    return receipt


def default_state() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pixel-control"


def default_onboarding() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "pixel-deployment" / "onboarding.json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exact private Frontier custom-budget editor")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--state", type=Path, default=default_state())
    parser.add_argument("--onboarding", type=Path, default=default_onboarding())
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("preview")
    for field, flag in (
        ("windowSeconds", "--window-seconds"), ("maxJobs", "--max-jobs"),
        ("maxInputTokens", "--max-input-tokens"), ("maxOutputTokens", "--max-output-tokens"),
        ("maxFailures", "--max-failures"), ("maxEstimatedCostMicros", "--max-estimated-cost-micros"),
    ):
        preview.add_argument(flag, dest=field, required=field != "maxEstimatedCostMicros", type=int)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--proposal-id", required=True)
    apply.add_argument("--proposal-hash", required=True)
    apply.add_argument("--confirm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        if args.command == "preview":
            request = {"schemaVersion": 1, **{field: getattr(args, field) for field in BUDGET_FIELDS}}
            print(json.dumps(create_proposal(args.root.resolve(), args.state, args.onboarding, request), indent=2))
            return 0
        if not args.confirm:
            raise BudgetInputError("applying a custom budget requires --confirm")
        print(json.dumps(apply_proposal(
            args.root.resolve(), args.state, args.onboarding, args.proposal_id, args.proposal_hash,
        ), indent=2))
        return 0
    except BudgetError as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
