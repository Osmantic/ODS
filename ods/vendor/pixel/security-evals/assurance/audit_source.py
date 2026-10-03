#!/usr/bin/env python3
"""Scan Pixel source and optional Git history without printing secret values."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]

REVIEWED_BLOBS_PATH = ROOT / "security-evals" / "historical-secret-closure" / "reviewed-blobs.json"
_REVIEWED_POLICY_SPEC = importlib.util.spec_from_file_location(
    "pixel_reviewed_policy", ROOT / "security-evals" / "assurance" / "reviewed_policy.py",
)
if _REVIEWED_POLICY_SPEC is None or _REVIEWED_POLICY_SPEC.loader is None:
    raise RuntimeError("could not load the shared reviewed-blob policy module")
REVIEWED_POLICY = importlib.util.module_from_spec(_REVIEWED_POLICY_SPEC)
_REVIEWED_POLICY_SPEC.loader.exec_module(REVIEWED_POLICY)
MAX_OBJECT_BYTES = 50 * 1024 * 1024
PLACEHOLDERS = {b"redacted", b"changeme", b"example", b"placeholder", b"local-no-auth", b"{token}"}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "google-api-key": re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "github-token": re.compile(rb"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "aws-access-key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack-token": re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    "stripe-live-key": re.compile(rb"\b[rs]k_live_[0-9A-Za-z]{16,}\b"),
    "discord-token": re.compile(rb"\b(?:mfa\.[\w-]{40,}|[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,40})\b"),
    "google-refresh-token": re.compile(rb'"refresh_token"\s*:\s*"(?P<secret>[^"\r\n]{8,})"', re.I),
    "oauth-client-secret": re.compile(rb'"client_secret"\s*:\s*"(?P<secret>[^"\r\n]{8,})"', re.I),
    "gateway-token": re.compile(rb"OPENCLAW_GATEWAY_TOKEN\s*=\s*[\"']?(?P<secret>[^\s#\"']+)", re.I),
}


class AuditError(RuntimeError):
    pass


def run_git(root: Path, *args: str, binary: bool = False):
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True,
        text=not binary,
    )
    if result.returncode:
        error = result.stderr if not binary else result.stderr.decode(errors="replace")
        raise AuditError(error.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def is_placeholder(match: re.Match[bytes]) -> bool:
    if "secret" not in match.re.groupindex:
        return False
    value = match.group("secret").strip().lower()
    return value in PLACEHOLDERS or value.startswith((b"${", b"{{", b"__pixel_"))


def labels_for(payload: bytes) -> list[str]:
    labels = []
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(payload):
            if not is_placeholder(match):
                labels.append(label)
                break
    return labels


def current_paths(root: Path) -> list[str]:
    raw = run_git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", binary=True)
    return [item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item]


def scan_current(root: Path) -> tuple[list[dict[str, object]], list[str]]:
    findings = []
    skipped = []
    for relative in current_paths(root):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        size = path.stat().st_size
        if size > MAX_OBJECT_BYTES:
            skipped.append(relative)
            continue
        labels = labels_for(path.read_bytes())
        if labels:
            findings.append({"path": relative.replace("\\", "/"), "labels": labels})
    return findings, skipped


def history_objects(root: Path) -> list[tuple[str, str]]:
    objects = run_git(root, "rev-list", "--objects", "--all")
    unique: dict[str, str] = {}
    for line in objects.splitlines():
        object_id, _, path = line.partition(" ")
        if path and object_id not in unique:
            unique[object_id] = path
    return list(unique.items())


def batch_object_payloads(root: Path, objects: list[tuple[str, str]]):
    request = b"".join(f"{object_id}\n".encode("ascii") for object_id, _ in objects)
    result = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=root, check=False,
        input=request, capture_output=True,
    )
    if result.returncode:
        raise AuditError(result.stderr.decode(errors="replace").strip() or "git cat-file --batch failed")
    paths = {object_id: path for object_id, path in objects}
    offset = 0
    while offset < len(result.stdout):
        end = result.stdout.find(b"\n", offset)
        if end < 0:
            raise AuditError("truncated git cat-file batch header")
        header = result.stdout[offset:end].decode("ascii", errors="strict").split()
        if len(header) != 3:
            raise AuditError("unexpected git cat-file batch header")
        object_id, object_type, raw_size = header
        size = int(raw_size)
        start = end + 1
        finish = start + size
        if finish >= len(result.stdout) or result.stdout[finish:finish + 1] != b"\n":
            raise AuditError("truncated git cat-file batch payload")
        yield object_id, object_type, size, result.stdout[start:finish], paths[object_id]
        offset = finish + 1


def scan_history(root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    findings = []
    skipped = []
    objects = history_objects(root)
    for object_id, object_type, size, payload, path in batch_object_payloads(root, objects):
        if object_type != "blob":
            continue
        if size > MAX_OBJECT_BYTES:
            skipped.append({"object": object_id, "path": path, "bytes": size})
            continue
        labels = labels_for(payload)
        if labels:
            findings.append({"object": object_id, "path": path, "labels": labels})
    return findings, skipped


def dependency_findings(root: Path) -> list[dict[str, str]]:
    findings = []
    for directory in ("plugin", "plugin-ops"):
        package_path = root / directory / "package.json"
        lock_path = root / directory / "package-lock.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for name, spec in package.get("dependencies", {}).items():
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", spec):
                findings.append({"path": f"{directory}/package.json", "label": f"non-exact dependency: {name}"})
            locked_spec = lock.get("packages", {}).get("", {}).get("dependencies", {}).get(name)
            if locked_spec != spec:
                findings.append({"path": f"{directory}/package-lock.json", "label": f"root lock mismatch: {name}"})
            installed = lock.get("packages", {}).get(f"node_modules/{name}", {})
            if installed.get("version") != spec or not str(installed.get("integrity", "")).startswith("sha512-"):
                findings.append({"path": f"{directory}/package-lock.json", "label": f"unverified lock entry: {name}"})
    requirements = root / "deploy" / "web-courier" / "requirements.lock"
    records: list[tuple[int, str]] = []
    pending = ""
    start = 0
    for number, line in enumerate(requirements.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not pending and (not value or value.startswith("#")):
            continue
        if not pending:
            start = number
        continued = value.endswith("\\")
        pending += (" " if pending else "") + (value[:-1].rstrip() if continued else value)
        if not continued:
            records.append((start, pending))
            pending = ""
    if pending:
        findings.append({"path": "deploy/web-courier/requirements.lock", "label": f"unterminated requirement at line {start}"})
    requirement_pattern = re.compile(
        r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?:\s+--hash=sha256:[0-9a-f]{64})+"
    )
    for number, value in records:
        if not requirement_pattern.fullmatch(value):
            findings.append({"path": "deploy/web-courier/requirements.lock", "label": f"requirement is not exact and fully SHA-256 locked at line {number}"})
            continue
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", value)
        if len(hashes) != len(set(hashes)):
            findings.append({"path": "deploy/web-courier/requirements.lock", "label": f"duplicate wheel hash at line {number}"})
    return findings


def reviewed_policy_violations(
    root: Path, *, enforce_missing: bool = True
) -> tuple[dict[str, object], list[str]]:
    """Validate every reviewed policy entry against the actual repository history.

    Binds payload SHA-256, the complete reachable path set, and the exact sorted
    audit_source labels. Any payload drift, path drift, extra reachable path for a
    credential-shaped reviewed blob, extra/missing label, or stale missing
    nonempty audit-source fixture entry fails closed. `enforce_missing` is the
    strict default; it may only be disabled by an explicit unit-test API boundary
    and is never inferred from the repository path. Never emits matched literal
    values.
    """
    try:
        policy = REVIEWED_POLICY.load()
    except REVIEWED_POLICY.ReviewedPolicyError as exc:
        raise AuditError(f"reviewed policy is invalid: {exc}") from exc
    violations: list[str] = []
    reachable_paths: dict[str, set[str]] = {}
    for line in run_git(root, "rev-list", "--objects", "--all").splitlines():
        object_id, _, path = line.partition(" ")
        if path:
            reachable_paths.setdefault(object_id, set()).add(path)
    for entry in policy["reviewedBlobs"]:
        object_id = entry["blobSha1"]
        reachable = reachable_paths.get(object_id)
        if reachable is None:
            if enforce_missing:
                violations.append(f"{object_id}: reviewed blob is not reachable in history")
            continue
        payload = run_git(root, "cat-file", "blob", object_id, binary=True)
        if hashlib.sha256(payload).hexdigest() != entry["payloadSha256"]:
            violations.append(f"{object_id}: payload SHA-256 drift")
        if entry["path"] not in reachable:
            violations.append(f"{object_id}: path drift (expected {entry['path']})")
        if entry["auditSourceLabels"] and len(reachable) > 1:
            violations.append(f"{object_id}: reviewed blob is reachable at an additional path")
        computed = sorted(labels_for(payload))
        expected = entry["auditSourceLabels"]
        if computed != expected:
            violations.append(
                f"{object_id}: audit_source label mismatch (expected {expected}, computed {computed})"
            )
    return policy, violations


def classify_history_findings(
    findings: list[dict[str, object]], policy: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split history findings into acknowledged fixtures and blocking findings.

    A finding is acknowledged only when its exact active-history tuple (blob
    SHA-1, path, sorted audit_source labels) matches a reviewed policy entry the
    source auditor actually flags. Anything else blocks.
    """
    acknowledged_oids = {entry["blobSha1"] for entry in REVIEWED_POLICY.acknowledged_entries(policy)}
    blocking: list[dict[str, object]] = []
    acknowledged: list[dict[str, object]] = []
    for finding in findings:
        entry = policy["byBlob"].get(finding["object"])
        if (
            entry is not None
            and finding["object"] in acknowledged_oids
            and finding["path"] == entry["path"]
            and sorted(finding["labels"]) == entry["auditSourceLabels"]
        ):
            acknowledged.append({
                "object": finding["object"],
                "path": finding["path"],
                "auditSourceLabels": sorted(finding["labels"]),
                "payloadSha256": entry["payloadSha256"],
            })
        else:
            blocking.append(finding)
    return blocking, acknowledged


def audit(
    root: Path, include_history: bool, *, enforce_missing: bool = True
) -> dict[str, object]:
    root = root.resolve()
    current, current_skipped = scan_current(root)
    history: list[dict[str, object]] = []
    history_skipped: list[dict[str, object]] = []
    if include_history:
        history, history_skipped = scan_history(root)
    dependencies = dependency_findings(root)
    policy, violations = reviewed_policy_violations(root, enforce_missing=enforce_missing)
    if include_history:
        blocking_history, acknowledged = classify_history_findings(history, policy)
    else:
        blocking_history, acknowledged = [], []
    return {
        "schemaVersion": 2,
        "secretValuesEmitted": False,
        "reviewedPolicy": {
            "schemaVersion": policy["schemaVersion"],
            "repository": policy["repository"],
            "sha256": REVIEWED_POLICY.policy_sha256(),
            "reviewedBlobCount": len(policy["reviewedBlobs"]),
            "acknowledgedBlobCount": len(REVIEWED_POLICY.acknowledged_entries(policy)),
        },
        "policyViolations": violations,
        "current": {"findings": current, "skippedLargeFiles": current_skipped},
        "history": {
            "scanned": include_history,
            "findings": blocking_history,
            "acknowledgedFixtures": acknowledged,
            "skippedLargeObjects": history_skipped,
        },
        "dependencies": {"findings": dependencies},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan every reachable historical blob")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = audit(args.root, args.history)
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"source audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    finding_count = sum(len(section["findings"]) for section in (
        report["current"], report["history"], report["dependencies"]
    ))
    skipped_count = len(report["current"]["skippedLargeFiles"]) + len(report["history"]["skippedLargeObjects"])
    return 1 if finding_count or skipped_count or report["policyViolations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
