#!/usr/bin/env python3
"""Audit active Pixel refs separately from exact acknowledged legacy refs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path(__file__).with_name("remote-ref-policy.json")
AUDITOR_PATH = Path(__file__).with_name("audit_source.py")
AUDITOR_SPEC = importlib.util.spec_from_file_location("pixel_audit_source", AUDITOR_PATH)
if AUDITOR_SPEC is None or AUDITOR_SPEC.loader is None:
    raise RuntimeError("could not load source auditor")
AUDITOR = importlib.util.module_from_spec(AUDITOR_SPEC)
AUDITOR_SPEC.loader.exec_module(AUDITOR)

REVIEWED_PATH = Path(__file__).with_name("reviewed_policy.py")
REVIEWED_SPEC = importlib.util.spec_from_file_location("pixel_reviewed_policy", REVIEWED_PATH)
if REVIEWED_SPEC is None or REVIEWED_SPEC.loader is None:
    raise RuntimeError("could not load the shared reviewed-blob policy module")
REVIEWED_POLICY = importlib.util.module_from_spec(REVIEWED_SPEC)
REVIEWED_SPEC.loader.exec_module(REVIEWED_POLICY)

REF_PATTERNS = ("refs/heads/*", "refs/tags/*", "refs/pull/*/head")
ALLOWED_REF = re.compile(
    r"refs/(?:heads|tags)/[^\s^~:?*\\\[\]]+|refs/pull/[1-9][0-9]*/head"
)
ALLOWED_OBJECT = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
EXACT_COMMIT = re.compile(r"[0-9a-f]{40}")
GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?$",
    re.I,
)


class RemoteAuditError(RuntimeError):
    pass


def run_git(root: Path, *args: str, safe_error: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise RemoteAuditError(safe_error or result.stderr.strip() or "git command failed")
    return result.stdout


def git_succeeds(root: Path, *args: str) -> bool:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode not in (0, 1):
        raise RemoteAuditError("git ancestry check failed")
    return result.returncode == 0


def advertised_refs(root: Path, remote: str) -> list[tuple[str, str]]:
    output = run_git(
        root, "ls-remote", "--refs", remote, *REF_PATTERNS,
        safe_error="could not list the configured remote refs",
    )
    refs: list[tuple[str, str]] = []
    for line in output.splitlines():
        object_id, separator, ref = line.partition("\t")
        if not separator or not ALLOWED_OBJECT.fullmatch(object_id) or not ALLOWED_REF.fullmatch(ref):
            raise RemoteAuditError("remote advertised an unexpected ref record")
        refs.append((object_id.lower(), ref))
    return sorted(set(refs), key=lambda item: item[1])


def remote_location(root: Path, remote: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", remote], cwd=root, check=False,
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else remote


def remote_identity(location: str) -> str:
    match = GITHUB_REMOTE.fullmatch(location)
    if match:
        return match.group(1)
    return str(Path(location).resolve())


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def load_policy(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RemoteAuditError("cannot read the remote-ref policy") from exc
    try:
        value = REVIEWED_POLICY.strict_json_loads(text)
    except REVIEWED_POLICY.ReviewedPolicyError as exc:
        raise RemoteAuditError(f"remote-ref policy is malformed: {exc}") from exc
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion", "repository", "lineageRootCommit", "reviewedBlobsSha256", "legacyRefs",
    }:
        raise RemoteAuditError("remote-ref policy has an unexpected shape")
    if value.get("schemaVersion") != 1:
        raise RemoteAuditError("remote-ref policy schema is unsupported")
    repository = value.get("repository")
    root = value.get("lineageRootCommit")
    reviewed_digest = value.get("reviewedBlobsSha256")
    entries = value.get("legacyRefs")
    if (
        not isinstance(repository, str) or not repository
        or not isinstance(root, str) or not EXACT_COMMIT.fullmatch(root)
        or not isinstance(reviewed_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", reviewed_digest)
    ):
        raise RemoteAuditError("remote-ref policy identity is invalid")
    if not isinstance(entries, list):
        raise RemoteAuditError("remote-ref policy legacy refs are invalid")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"ref", "object", "reason"}:
            raise RemoteAuditError("remote-ref policy legacy entry has an unexpected shape")
        ref, object_id, reason = entry["ref"], entry["object"], entry["reason"]
        if (
            not isinstance(ref, str) or not ALLOWED_REF.fullmatch(ref)
            or not isinstance(object_id, str) or not ALLOWED_OBJECT.fullmatch(object_id)
            or not isinstance(reason, str) or not reason.strip() or ref in seen
        ):
            raise RemoteAuditError("remote-ref policy legacy entry is invalid")
        entry["object"] = object_id.lower()
        seen.add(ref)
    return value


def resolve_commit(root: Path, ref: str) -> str:
    commit = run_git(root, "rev-parse", f"{ref}^{{commit}}", safe_error="remote ref is not commit-like").strip()
    if not EXACT_COMMIT.fullmatch(commit):
        raise RemoteAuditError("remote ref did not resolve to an exact commit")
    return commit


def lineage_commits(root: Path, lineage_root: str, tips: list[str]) -> list[str]:
    commits = {lineage_root}
    for tip in tips:
        output = run_git(
            root, "rev-list", "--ancestry-path", f"{lineage_root}..{tip}",
            safe_error="could not enumerate the active Pixel lineage",
        )
        commits.update(output.splitlines())
    return sorted(commits)


def reachable_commits(root: Path, tips: list[str]) -> list[str]:
    if not tips:
        return []
    output = run_git(
        root, "rev-list", *tips, safe_error="could not enumerate acknowledged legacy history",
    )
    return sorted(set(output.splitlines()))


def commit_tree_objects(root: Path, commits: list[str]) -> list[tuple[str, str]]:
    objects: dict[str, str] = {}
    for commit in commits:
        raw = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--full-tree", commit], cwd=root,
            check=False, capture_output=True,
        )
        if raw.returncode:
            raise RemoteAuditError("could not enumerate a remote commit tree")
        for record in raw.stdout.split(b"\0"):
            if not record:
                continue
            metadata, separator, encoded_path = record.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3 or fields[1] != b"blob":
                continue
            object_id = fields[2].decode("ascii", errors="strict")
            path = encoded_path.decode("utf-8", errors="surrogateescape")
            objects.setdefault(object_id, path)
    return sorted(objects.items())


def scan_commits(root: Path, commits: list[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    findings: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    objects = commit_tree_objects(root, commits)
    for object_id, object_type, size, payload, path in AUDITOR.batch_object_payloads(root, objects):
        if object_type != "blob":
            continue
        if size > AUDITOR.MAX_OBJECT_BYTES:
            skipped.append({"object": object_id, "path": path, "bytes": size})
            continue
        labels = AUDITOR.labels_for(payload)
        if labels:
            findings.append({"object": object_id, "path": path, "labels": labels})
    return findings, skipped


def classify_history_findings(
    findings: list[dict[str, object]], policy: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
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


def audit_remote_refs(
    root: Path, remote: str, policy_path: Path = DEFAULT_POLICY, *, enforce_missing: bool = True
) -> dict[str, object]:
    root = root.resolve()
    policy = load_policy(policy_path.resolve())
    refs = advertised_refs(root, remote)
    legacy_policy = {entry["ref"]: entry for entry in policy["legacyRefs"]}
    advertised = {ref: object_id for object_id, ref in refs}
    violations: list[dict[str, str]] = []
    active_refs: list[dict[str, str]] = []
    legacy_refs: list[dict[str, str]] = []
    location = remote_location(root, remote)
    identity = remote_identity(location)
    if identity.casefold() != str(policy["repository"]).casefold():
        violations.append({"code": "remote-identity-mismatch", "ref": ""})

    try:
        reviewed_policy = REVIEWED_POLICY.load()
        reviewed_policy_digest = REVIEWED_POLICY.policy_sha256()
    except REVIEWED_POLICY.ReviewedPolicyError as exc:
        raise RemoteAuditError(f"reviewed policy is invalid: {exc}") from exc
    if policy["reviewedBlobsSha256"] != reviewed_policy_digest:
        violations.append({"code": "reviewed-policy-hash-drift", "ref": ""})

    for ref, entry in legacy_policy.items():
        actual = advertised.get(ref)
        if actual is not None and actual != entry["object"]:
            violations.append({"code": "legacy-ref-drift", "ref": ref})

    with tempfile.TemporaryDirectory(prefix="pixel-remote-ref-audit-") as temporary:
        mirror = Path(temporary) / "mirror.git"
        run_git(root, "init", "--bare", "-q", str(mirror))
        run_git(
            mirror, "remote", "add", "source", location,
            safe_error="could not configure the temporary audit mirror",
        )
        for index, (object_id, ref) in enumerate(refs):
            local_ref = f"refs/audit/{index}"
            run_git(
                mirror, "fetch", "--quiet", "--no-tags", "--force", "source",
                f"{ref}:{local_ref}", safe_error=f"could not fetch remote ref {ref}",
            )
            if ref in legacy_policy:
                if object_id == legacy_policy[ref]["object"]:
                    legacy_refs.append({
                        "object": object_id,
                        "ref": ref,
                        "reason": legacy_policy[ref]["reason"],
                        "commit": resolve_commit(mirror, local_ref),
                    })
                continue
            try:
                commit = resolve_commit(mirror, local_ref)
            except RemoteAuditError:
                violations.append({"code": "non-commit-ref", "ref": ref})
                continue
            if git_succeeds(mirror, "merge-base", "--is-ancestor", policy["lineageRootCommit"], commit):
                active_refs.append({"object": object_id, "ref": ref, "commit": commit})
            else:
                violations.append({"code": "unacknowledged-non-lineage-ref", "ref": ref})

        missing_legacy = sorted(set(legacy_policy) - set(advertised))
        active_tips = sorted({entry["commit"] for entry in active_refs})
        legacy_tips = sorted({entry["commit"] for entry in legacy_refs})
        if active_tips:
            active_findings, active_skipped = scan_commits(
                mirror, lineage_commits(mirror, policy["lineageRootCommit"], active_tips),
            )
        else:
            active_findings, active_skipped = [], []
            violations.append({"code": "no-active-pixel-ref", "ref": ""})
        blocking_active, acknowledged_fixtures = classify_history_findings(active_findings, reviewed_policy)
        _, reviewed_violations = AUDITOR.reviewed_policy_violations(
            mirror, enforce_missing=enforce_missing,
        )
        for violation in reviewed_violations:
            violations.append({"code": "reviewed-policy-violation", "ref": "", "detail": violation})
        legacy_findings, legacy_skipped = scan_commits(mirror, reachable_commits(mirror, legacy_tips))

    source_head = run_git(root, "rev-parse", "HEAD", safe_error="could not resolve source HEAD").strip()
    source_advertised = source_head in {entry["commit"] for entry in active_refs}
    if not source_advertised:
        violations.append({"code": "source-head-not-advertised", "ref": "HEAD"})
    blockers = list(violations)
    if blocking_active:
        blockers.append({"code": "active-lineage-secret-finding", "ref": ""})
    if active_skipped:
        blockers.append({"code": "active-lineage-skipped-object", "ref": ""})
    status = "pass" if not blockers else "fail"
    return {
        "schemaVersion": 2,
        "secretValuesEmitted": False,
        "status": status,
        "policy": {
            "repository": policy["repository"],
            "lineageRootCommit": policy["lineageRootCommit"],
            "reviewedBlobsSha256": policy["reviewedBlobsSha256"],
            "sha256": canonical_sha256(policy),
        },
        "remoteRefCount": len(refs),
        "remoteIdentity": identity,
        "acknowledgedFixtures": sorted(acknowledged_fixtures, key=lambda item: item["object"]),
        "releaseGate": {
            "status": status,
            "sourceHead": source_head,
            "sourceHeadAdvertised": source_advertised,
            "refs": sorted(active_refs, key=lambda item: item["ref"]),
            "history": {"findings": blocking_active, "skippedLargeObjects": active_skipped},
            "policyViolations": violations,
            "blockers": blockers,
        },
        "legacyHistory": {
            "status": "reported",
            "refs": sorted(legacy_refs, key=lambda item: item["ref"]),
            "retiredPolicyRefs": missing_legacy,
            "history": {"findings": legacy_findings, "skippedLargeObjects": legacy_skipped},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    try:
        report = audit_remote_refs(args.root, args.remote, args.policy)
    except (RemoteAuditError, AUDITOR.AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"remote-ref audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
