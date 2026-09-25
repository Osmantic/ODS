#!/usr/bin/env python3
"""Validate Pixel's qualification contract and build a content-free readiness index."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any, Callable


MAX_JSON_BYTES = 2 * 1024 * 1024
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
BOUNDARY = (
    "Content-free readiness index only; evidence authenticity and private details remain "
    "in separately reviewed owner-controlled records."
)
GATES = (
    "automated-release-matrix",
    "supported-host-systemd-matrix",
    "deep-work-event-horizon",
    "recovery-security-matrix",
    "model-capability-contract",
    "outcome-parity",
    "signed-release-lifecycle",
    "owner-usability",
    "frontier-live-qualification",
    "multi-provider-local-first",
    "historical-secret-closure",
    "distribution-license",
)
EXTERNAL_GATES = tuple(gate for gate in GATES if gate != "model-capability-contract")
MODEL_ROWS = (
    ("under-8", "remote-or-compact", "start-small"),
    ("8-15", "compact-local", "start-small"),
    ("16-31", "balanced-local", "moderate"),
    ("32-63", "larger-local", "expanded-after-measurement"),
    ("64-plus", "large-memory-local", "expanded-after-measurement"),
)
HOST_LANES = {
    "ubuntu-24.04-automated": ("Ubuntu 24.04 LTS", "github-hosted"),
    "debian-12-automated": ("Debian 12", "manifest-pinned-container"),
    "ubuntu-24.04-systemd": ("Ubuntu 24.04 LTS", "deployment-owned-systemd"),
    "debian-12-systemd": ("Debian 12", "deployment-owned-systemd"),
}


class ReadinessError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def parse_json(payload: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise ReadinessError(f"{label} contains duplicate fields")
            value[key] = child
        return value

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ReadinessError(f"{label} contains a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label} is not valid JSON") from exc


def read_bytes(path: Path, *, private: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReadinessError("qualification input is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= MAX_JSON_BYTES:
            raise ReadinessError("qualification input is not a bounded single-link file")
        if private and os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ReadinessError("private qualification claims must be owner-bound mode 0600")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise ReadinessError("qualification input is oversized")
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise ReadinessError("qualification input changed during read")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_json(path: Path, label: str, *, private: bool = False) -> dict[str, Any]:
    value = parse_json(read_bytes(path, private=private), label)
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must be an object")
    return value


def validate_matrix(root: Path) -> dict[str, Any]:
    matrix = read_json(root / "QUALIFICATION-MATRIX.json", "qualification matrix")
    manifest = read_json(root / "RELEASE-MANIFEST.json", "release manifest")
    if set(matrix) != {
        "$schema", "schemaVersion", "supportedHosts", "hostLanes", "modelCapacity",
        "capabilityProfiles", "promotion",
    } or matrix.get("$schema") != "./schemas/qualification-matrix-v1.schema.json" or matrix.get("schemaVersion") != 1:
        raise ReadinessError("qualification matrix identity is invalid")
    if matrix.get("supportedHosts") != manifest.get("supportedHosts") or matrix["supportedHosts"] != [
        "Ubuntu 24.04 LTS", "Debian 12",
    ]:
        raise ReadinessError("qualification host contract differs from the release manifest")
    lanes = matrix.get("hostLanes")
    if not isinstance(lanes, list) or len(lanes) != len(HOST_LANES):
        raise ReadinessError("qualification host lanes are incomplete")
    observed_lanes: dict[str, dict[str, Any]] = {}
    lane_fields = {
        "id", "host", "environment", "requiredForPromotion", "providerCallsAllowed",
        "credentialInputsAllowed", "checks",
    }
    allowed_checks = {
        "static-unit-security", "clean-room-lifecycle", "plugin-integrity", "release-package",
        "reproducible-package", "install-apply-verify", "service-isolation", "sandbox-isolation", "deep-work-real-crash-endurance", "deep-work-supervised-service", "capability-pack-live-runtime",
        "backup-recovery", "knowledge-vault-backup-recovery", "release-identity-refusal", "disposable-gateway-removal", "degraded-recovery", "owner-ui-reachability",
    }
    expected_checks = {
        "ubuntu-24.04-automated": ["static-unit-security", "clean-room-lifecycle", "plugin-integrity", "release-package"],
        "debian-12-automated": ["static-unit-security", "clean-room-lifecycle", "plugin-integrity", "release-package", "reproducible-package"],
        "ubuntu-24.04-systemd": ["install-apply-verify", "service-isolation", "sandbox-isolation", "deep-work-real-crash-endurance", "deep-work-supervised-service", "capability-pack-live-runtime", "backup-recovery", "knowledge-vault-backup-recovery", "release-identity-refusal", "disposable-gateway-removal", "degraded-recovery", "owner-ui-reachability"],
        "debian-12-systemd": ["install-apply-verify", "service-isolation", "sandbox-isolation", "deep-work-real-crash-endurance", "deep-work-supervised-service", "capability-pack-live-runtime", "backup-recovery", "knowledge-vault-backup-recovery", "release-identity-refusal", "disposable-gateway-removal", "degraded-recovery", "owner-ui-reachability"],
    }
    for lane in lanes:
        if not isinstance(lane, dict) or set(lane) != lane_fields or not isinstance(lane.get("id"), str):
            raise ReadinessError("qualification host lane shape is invalid")
        lane_id = lane["id"]
        if lane_id in observed_lanes or lane_id not in HOST_LANES:
            raise ReadinessError("qualification host lane identity is invalid")
        expected_host, expected_environment = HOST_LANES[lane_id]
        checks = lane.get("checks")
        if (
            lane.get("host") != expected_host or lane.get("environment") != expected_environment
            or lane.get("requiredForPromotion") is not True
            or lane.get("providerCallsAllowed") is not False
            or lane.get("credentialInputsAllowed") is not False
            or not isinstance(checks, list) or not 4 <= len(checks) <= 12
            or len(set(checks)) != len(checks) or not set(checks).issubset(allowed_checks)
            or checks != expected_checks[lane_id]
        ):
            raise ReadinessError("qualification host lane authority or coverage is invalid")
        observed_lanes[lane_id] = lane
    if set(observed_lanes) != set(HOST_LANES):
        raise ReadinessError("qualification host lanes are incomplete")

    rows = matrix.get("modelCapacity")
    if not isinstance(rows, list) or len(rows) != len(MODEL_ROWS):
        raise ReadinessError("model-capability matrix is incomplete")
    observed_rows = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "memoryCapacityGiB", "localModelClass", "contextGuidance", "fitIsGuaranteed",
            "requiredValidation",
        }:
            raise ReadinessError("model-capability row shape is invalid")
        observed_rows.append((row["memoryCapacityGiB"], row["localModelClass"], row["contextGuidance"]))
        if row.get("fitIsGuaranteed") is not False or row.get("requiredValidation") != [
            "synthetic-task-contract", "latency-and-memory-measurement", "owner-quality-review",
        ]:
            raise ReadinessError("model-capability validation boundary is invalid")
    if tuple(observed_rows) != MODEL_ROWS:
        raise ReadinessError("model-capability rows differ from Pixel Doctor")

    profiles = matrix.get("capabilityProfiles")
    expected_profiles = manifest.get("capabilityProfiles")
    if not isinstance(profiles, list) or [item.get("id") for item in profiles if isinstance(item, dict)] != expected_profiles:
        raise ReadinessError("qualification capability profiles differ from the release manifest")
    for item in profiles:
        if not isinstance(item, dict) or set(item) != {"id", "limbs"}:
            raise ReadinessError("qualification capability profile shape is invalid")
        profile = read_json(root / "profiles" / "capabilities" / f"{item['id']}.json", "capability profile")
        if item["limbs"] != profile.get("limbs"):
            raise ReadinessError("qualification capability profile differs from its source profile")
    promotion = matrix.get("promotion")
    if not isinstance(promotion, dict) or promotion != {
        "requiredConsecutivePasses": 2,
        "requiredGates": list(GATES),
        "automatedProviderCallsAllowed": False,
        "publicationRequiresAllGates": True,
    }:
        raise ReadinessError("qualification promotion contract is invalid")
    return matrix


def validate_claims(value: Any) -> tuple[int, dict[str, dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "consecutivePasses", "gates"} or value.get("schemaVersion") != 1:
        raise ReadinessError("private promotion claims have missing or unknown fields")
    passes = value.get("consecutivePasses")
    gates = value.get("gates")
    if type(passes) is not int or not 0 <= passes <= 1000 or not isinstance(gates, dict) or set(gates) != set(EXTERNAL_GATES):
        raise ReadinessError("private promotion claims are incomplete")
    normalized: dict[str, dict[str, Any]] = {}
    for gate in EXTERNAL_GATES:
        claim = gates[gate]
        if not isinstance(claim, dict) or set(claim) != {"status", "evidenceSha256"}:
            raise ReadinessError("private promotion gate shape is invalid")
        status_value, evidence = claim.get("status"), claim.get("evidenceSha256")
        if status_value not in {"pass", "blocked"}:
            raise ReadinessError("private promotion gate status is invalid")
        if status_value == "pass":
            if not isinstance(evidence, str) or HASH_RE.fullmatch(evidence) is None:
                raise ReadinessError("passing promotion gate requires an exact evidence hash")
        elif evidence is not None:
            raise ReadinessError("blocked promotion gate cannot claim evidence")
        normalized[gate] = {"status": status_value, "evidenceSha256": evidence}
    return passes, normalized


def default_claims() -> tuple[int, dict[str, dict[str, Any]]]:
    return 0, {
        gate: {"status": "blocked", "evidenceSha256": None} for gate in EXTERNAL_GATES
    }


def git_identity(root: Path) -> tuple[str, str]:
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if status_result.returncode or status_result.stdout:
        raise ReadinessError("promotion readiness requires a clean source tree")
    values = []
    for revision in ("HEAD", "HEAD^{tree}"):
        result = subprocess.run(
            ["git", "rev-parse", revision], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        value = result.stdout.strip()
        if result.returncode or COMMIT_RE.fullmatch(value) is None:
            raise ReadinessError("promotion source identity is unavailable")
        values.append(value)
    return values[0], values[1]


def build_readiness(
    root: Path, claims: Any | None = None, *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    source_identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    matrix = validate_matrix(root)
    passes, external = default_claims() if claims is None else validate_claims(claims)
    matrix_sha = hashlib.sha256(read_bytes(root / "QUALIFICATION-MATRIX.json")).hexdigest()
    manifest_sha = hashlib.sha256(read_bytes(root / "RELEASE-MANIFEST.json")).hexdigest()
    source_commit, source_tree = source_identity or git_identity(root)
    gates = []
    for gate in GATES:
        claim = (
            {"status": "pass", "evidenceSha256": matrix_sha}
            if gate == "model-capability-contract" else external[gate]
        )
        gates.append({"id": gate, **claim})
    status_value = "pass" if (
        passes >= matrix["promotion"]["requiredConsecutivePasses"]
        and all(gate["status"] == "pass" for gate in gates)
    ) else "blocked"
    return {
        "schemaVersion": 1,
        "operation": "pixel-promotion-readiness",
        "pixel": read_bytes(root / "VERSION").decode("ascii").strip(),
        "sourceCommit": source_commit,
        "sourceTree": source_tree,
        "releaseManifestSha256": manifest_sha,
        "qualificationMatrixSha256": matrix_sha,
        "generatedAt": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status_value,
        "consecutivePasses": passes,
        "requiredConsecutivePasses": matrix["promotion"]["requiredConsecutivePasses"],
        "gates": gates,
        "privacy": {
            "pathsIncluded": False,
            "credentialsIncluded": False,
            "hostIdentityIncluded": False,
            "modelIdentityIncluded": False,
            "providerContentIncluded": False,
        },
        "boundary": BOUNDARY,
    }


def write_new_private(path: Path, payload: bytes) -> None:
    if not path.is_absolute() or path == Path(path.anchor):
        raise ReadinessError("promotion output must be an absolute non-root path")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if path.parent.resolve(strict=True) != path.parent:
            raise ReadinessError("promotion output directory contains a link")
    except OSError as exc:
        raise ReadinessError("promotion output directory is unavailable") from exc
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode):
        raise ReadinessError("promotion output directory is unsafe")
    if os.name != "nt" and (
        parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ReadinessError("promotion output directory must be owner-bound mode 0700")
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except OSError as exc:
            raise ReadinessError("promotion output already exists or is unsafe") from exc
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Pixel's content-free promotion readiness index")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--claims", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        root = args.root.resolve()
        claims_path = Path(os.path.abspath(args.claims)) if args.claims else None
        output_path = Path(os.path.abspath(args.output)) if args.output else None
        for path, label in ((claims_path, "claims"), (output_path, "output")):
            if path is not None and (path == root or root in path.parents):
                raise ReadinessError(f"promotion {label} must remain outside the source repository")
        if claims_path is not None:
            try:
                if claims_path.resolve(strict=True) != claims_path:
                    raise ReadinessError("private promotion claims path contains a link")
            except OSError as exc:
                raise ReadinessError("private promotion claims are unavailable") from exc
        claims = read_json(claims_path, "private promotion claims", private=True) if claims_path else None
        value = build_readiness(root, claims)
        payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if output_path:
            write_new_private(output_path, payload)
        print(payload.decode("utf-8"), end="")
        return 0 if value["status"] == "pass" else 3
    except (ReadinessError, UnicodeError, OSError) as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
