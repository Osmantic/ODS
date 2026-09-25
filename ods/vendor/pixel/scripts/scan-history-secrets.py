#!/usr/bin/env python3
"""Scan every blob across all git history for credential material and emit content-free closure evidence.

The working-tree policy check (scripts/check-no-secrets.sh) covers the current commit only.
The historical-secret-closure gate requires that no secret ever entered any commit. This walks
every unique blob reachable from every ref and applies the same credential pattern set. Its
output is content-free: it reports only counts, pattern-set identity, source identity, digests,
and — on failure — the offending blob object ids and labels, never the secret bytes themselves.

The reviewed-blob allowlist is a strict versioned contract (see
security-evals/assurance/reviewed_policy.py). Every entry must be bound by exact blob SHA-1,
raw payload SHA-256, exact path, the exact scanner labels, and the exact sorted audit_source
scanner labels. Any stale, drifted, or unreviewed finding fails the gate closed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path


# Kept in step with scripts/check-no-secrets.sh (a test asserts every label here is covered there).
PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Google refresh token", re.compile(r'"refresh_token"\s*:\s*"(?!REDACTED|CHANGEME|example)[^"{]{8,}"', re.I)),
    ("OAuth client secret", re.compile(r'"client_secret"\s*:\s*"(?!REDACTED|CHANGEME|example)[^"{]{8,}"', re.I)),
    ("Discord token", re.compile(r"\b(?:mfa\.[\w-]{40,}|[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,40})\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{30,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
]
GATEWAY_TOKEN = re.compile(r"OPENCLAW_GATEWAY_TOKEN\s*=\s*([^\s#]+)")
GATEWAY_PLACEHOLDERS = {"redacted", "changeme", "example", "${openclaw_gateway_token}"}
MAX_BLOB_BYTES = 2_000_000

ROOT = Path(__file__).resolve().parents[1]

_REVIEWED_POLICY_SPEC = importlib.util.spec_from_file_location(
    "pixel_reviewed_policy", ROOT / "security-evals" / "assurance" / "reviewed_policy.py",
)
_AUDIT_SOURCE_SPEC = importlib.util.spec_from_file_location(
    "pixel_audit_source_for_scanner", ROOT / "security-evals" / "assurance" / "audit_source.py",
)
if _REVIEWED_POLICY_SPEC is None or _REVIEWED_POLICY_SPEC.loader is None:
    raise RuntimeError("could not load the shared reviewed-blob policy module")
REVIEWED_POLICY = importlib.util.module_from_spec(_REVIEWED_POLICY_SPEC)
_REVIEWED_POLICY_SPEC.loader.exec_module(REVIEWED_POLICY)
if _AUDIT_SOURCE_SPEC is None or _AUDIT_SOURCE_SPEC.loader is None:
    raise RuntimeError("could not load the source auditor for label verification")
AUDIT_SOURCE = importlib.util.module_from_spec(_AUDIT_SOURCE_SPEC)
_AUDIT_SOURCE_SPEC.loader.exec_module(AUDIT_SOURCE)


def pattern_set_sha256() -> str:
    identity = "\n".join(label + "::" + pattern.pattern for label, pattern in PATTERNS)
    identity += "\ngateway::" + GATEWAY_TOKEN.pattern
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def load_allowlist(path: Path | None) -> dict[str, object]:
    """Strictly load and validate a reviewed-blob allowlist.

    A missing or unreadable allowlist fails closed. The only empty-policy path is
    the explicit Python test API boundary (calling `scan` with no policy), which
    is never reachable from the CLI default.
    """
    if path is None:
        raise SystemExit("historical secret allowlist is required")
    allow_path = Path(path)
    if not allow_path.is_file():
        raise SystemExit(f"historical secret allowlist is missing: {allow_path}")
    try:
        return REVIEWED_POLICY.load(allow_path)
    except (REVIEWED_POLICY.ReviewedPolicyError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"historical secret allowlist is malformed: {exc}")


def _policy_sha256(policy: dict[str, object]) -> str | None:
    """Report the strictly validated document's own canonical SHA-256."""
    if not policy.get("reviewedBlobs"):
        return None
    if policy.get("policySha256"):
        return str(policy["policySha256"])
    doc = {key: value for key, value in policy.items() if key not in ("byBlob", "policySha256")}
    return REVIEWED_POLICY.canonical_sha256(doc)


def _scan_labels(text: str) -> list[str]:
    labels = []
    for label, pattern in PATTERNS:
        if pattern.search(text):
            labels.append(label)
    for match in GATEWAY_TOKEN.finditer(text):
        if match.group(1).strip("\"'").lower() not in GATEWAY_PLACEHOLDERS:
            labels.append("non-placeholder gateway token")
    return labels


def scan(root: Path, policy: dict[str, object] | None = None) -> dict[str, object]:
    policy = policy or {}

    def git(args: list[str]) -> bytes:
        return subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, check=True).stdout

    commits = int(git(["rev-list", "--all", "--count"]).decode().strip() or "0")
    source_commit = git(["rev-parse", "HEAD"]).decode().strip()
    source_tree = git(["rev-parse", "HEAD^{tree}"]).decode().strip()

    # Map every reachable blob object id to its complete reachable path set.
    paths: dict[str, set[str]] = {}
    for line in git(["rev-list", "--objects", "--all"]).decode("utf-8", errors="replace").splitlines():
        object_id, _, path = line.partition(" ")
        if path:
            paths.setdefault(object_id, set()).add(path)

    by_blob = policy.get("byBlob") or {}

    # Stream every unique object; scan the blobs.
    blobs = 0
    findings: list[str] = []
    finding_labels: dict[str, set[str]] = {}
    payload_sha256: dict[str, str] = {}
    reviewed_payload: dict[str, bytes] = {}
    command = ["git", "cat-file", "--batch-all-objects", "--batch"]
    with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE) as proc:
        if proc.stdout is None:
            raise RuntimeError("git object scanner did not expose its bounded output stream")
        stream = proc.stdout
        while True:
            header = stream.readline()
            if not header:
                break
            parts = header.split()
            if len(parts) != 3:
                # Missing/ambiguous object line has no trailing payload; continue.
                continue
            oid, otype, size = parts[0].decode(), parts[1].decode(), int(parts[2])
            payload = stream.read(size)
            terminator = stream.read(1)
            if len(payload) != size or terminator != b"\n":
                raise RuntimeError("git object scanner emitted a truncated or malformed object payload")
            if otype != "blob":
                continue
            blobs += 1
            if size > MAX_BLOB_BYTES:
                continue
            text = payload.decode("utf-8", "ignore")
            labels = _scan_labels(text)
            if labels:
                for label in labels:
                    findings.append(f"{oid}: {label}")
                finding_labels.setdefault(oid, set()).update(labels)
            if oid in by_blob:
                payload_sha256[oid] = hashlib.sha256(payload).hexdigest()
                reviewed_payload[oid] = payload
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, command)

    findings = sorted(set(findings))
    finding_oids = {f.split(":", 1)[0] for f in findings}
    unreviewed = sorted(f for f in findings if f.split(":", 1)[0] not in by_blob)

    violations: list[str] = []
    stale: list[str] = []
    for oid, entry in by_blob.items():
        if oid not in finding_oids:
            stale.append(oid)
            continue
        if payload_sha256.get(oid) != entry.get("payloadSha256"):
            violations.append(f"{oid}: payload SHA-256 drift")
        reachable = paths.get(oid) or set()
        if entry.get("path") not in reachable:
            violations.append(f"{oid}: path drift")
        if len(reachable) > 1:
            violations.append(f"{oid}: reviewed blob is reachable at an additional path")
        if finding_labels.get(oid) != set(entry.get("labels", [])):
            violations.append(f"{oid}: scanner label mismatch")
        computed = sorted(AUDIT_SOURCE.labels_for(reviewed_payload.get(oid, b"")))
        if computed != entry.get("auditSourceLabels", []):
            violations.append(f"{oid}: audit_source label mismatch")

    status = "closed" if not unreviewed and not stale and not violations else "open"
    return {
        "operation": "pixel-historical-secret-closure",
        "sourceCommit": source_commit,
        "sourceTree": source_tree,
        "commitsScanned": commits,
        "blobsScanned": blobs,
        "patternSetSha256": pattern_set_sha256(),
        "reviewedPolicySha256": _policy_sha256(policy),
        "findingsCount": len(findings),
        "reviewedCount": len([f for f in findings if f.split(":", 1)[0] in by_blob]),
        "unreviewedCount": len(unreviewed),
        "staleAllowlistCount": len(stale),
        "policyViolationCount": len(violations),
        "findings": findings,
        "unreviewed": unreviewed,
        "staleAllowlist": stale,
        "policyViolations": violations,
        "status": status,
    }


def main() -> int:
    args = [a for a in sys.argv[1:]]
    allow_path = None
    if "--allowlist" in args:
        i = args.index("--allowlist")
        allow_path = Path(args[i + 1])
        del args[i:i + 2]
    root = Path(args[0] if args else ".").resolve()
    default_allow = root / "security-evals" / "historical-secret-closure" / "reviewed-blobs.json"
    allowlist = load_allowlist(allow_path if allow_path is not None else default_allow)
    result = scan(root, allowlist)
    # Content-free evidence: object ids, labels, and digests only, never the secret bytes.
    summary = {k: v for k, v in result.items() if k not in {"findings", "unreviewed", "staleAllowlist", "policyViolations"}}
    print(json.dumps(summary, sort_keys=True))
    if result["status"] != "closed":
        print("historical secret closure FAILED:", file=sys.stderr)
        for f in result["unreviewed"]:
            print(f"- unreviewed: {f}", file=sys.stderr)
        for oid in result["staleAllowlist"]:
            print(f"- stale allowlist entry (no matching finding): {oid}", file=sys.stderr)
        for violation in result["policyViolations"]:
            print(f"- policy violation: {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
