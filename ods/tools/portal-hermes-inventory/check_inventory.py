#!/usr/bin/env python3
"""
Strict checker for Portal-on-Hermes predecessor preservation inventory.

Validates:
  - JSON Schema conformance (strict bool/int handling, no unknown fields)
  - External draft-07 schema validation via --schema; when requested it fails
    closed if jsonschema is unavailable
  - Immutable source binding to the exact #3385/#3818 base, head, path count,
    and source-delta digest
  - Canonical digest matches recomputed SHA-256 (64 hex chars)
  - Per-PR source_delta_sha256 over sorted change_type<TAB>path records
  - Path validation: POSIX repository-relative UTF-8 strings only
  - No duplicate (source_pr, path) identities
  - Non-empty rationale on every record
  - drop-blocked-user-approval requires non-empty user_approval_reference
  - Non-drop records must NOT have user_approval_reference
  - Total records matches actual record count
  - Subsystem totals are consistent
  - Disposition totals are consistent
  - Records are sorted by (source_pr, path)
  - All SHA fields are exactly 40 lowercase hex
  - canonical_digest is exactly 64 lowercase hex
  - Strict bool vs int at every nested level
  - No unknown fields at any nesting level

Exit 0 = pass, exit 1 = fail.
"""

import hashlib
import json
import re
import sys
from collections import Counter

# ── Patterns ──────────────────────────────────────────────────────────

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PR_KEY_RE = re.compile(r"^pr-[0-9]+$")

EXPECTED_SOURCE_PRS = {
    "pr-3385": {
        "base_sha": "21f4b3a64dd2a2fac1163f446806091c25b6b814",
        "head_sha": "53bf63fa256b6bed27764cace170d45aa43c75d1",
        "changed_path_count": 283,
        "source_delta_sha256": "6baaa5dd2bae01b297b75efc387d0dea88c3bb7c9eb7298a6562087e9a3e8c0b",
    },
    "pr-3818": {
        "base_sha": "58a2d544b8752cee2b42d54001100d9ef2e89ae0",
        "head_sha": "90e74b01c597bec13b12955ad04acb1f0bf68b16",
        "changed_path_count": 144,
        "source_delta_sha256": "e2f1f236e6a9e3fdfb4650ac58a0ac627b997e80e36ff9d496c07d3da39a9b22",
    },
}

# ── Enumerations ──────────────────────────────────────────────────────

VALID_SUBSYSTEMS = {
    "core-runtime", "ingress-edge", "model-routing", "providers",
    "access-scopes", "leases-approvals", "source-broker", "operations",
    "previews", "dashboard-api", "installer", "observability",
    "tests-docs", "other",
}

VALID_DISPOSITIONS = {
    "retain-semantics", "replace-openclaw-coupling", "already-on-main",
    "orthogonal-retain", "review-required", "drop-blocked-user-approval",
}

VALID_CHANGE_TYPES = {"A", "M", "D", "R", "T", "AM"}

VALID_CLASSIFICATION_METHODS = {"mechanical", "manual"}

# ── Top-level allowed keys ───────────────────────────────────────────

TOP_LEVEL_KEYS = {
    "version", "ledger_id", "generated_at_branch", "branch_sha",
    "source_prs", "total_records", "canonical_digest", "records",
    "subsystem_totals", "disposition_totals", "policy",
}

# ── Record allowed keys ──────────────────────────────────────────────

RECORD_KEYS = {
    "source_pr", "path", "change_type", "subsystem",
    "disposition", "classification_method", "base_sha", "head_sha",
    "rationale", "user_approval_reference",
}

# ── source_pr metadata keys ──────────────────────────────────────────

PR_META_KEYS = {
    "base_sha", "head_sha", "changed_path_count", "source_delta_sha256",
}

# ── Policy keys ──────────────────────────────────────────────────────

POLICY_KEYS = {
    "no_record_may_vanish", "no_record_may_duplicate",
    "no_record_may_move_to_drop_without_user_approval",
    "drop_requires",
}


# ── Loading ───────────────────────────────────────────────────────────

def load_inventory(path: str) -> dict:
    """Load and parse inventory JSON."""
    with open(path) as f:
        return json.load(f)


# ── Path validation ──────────────────────────────────────────────────

def validate_path(path_str: str, label: str) -> list:
    """Validate a POSIX repository-relative path. Return list of issues."""
    issues = []
    if not isinstance(path_str, str):
        issues.append(f"{label}: path must be string, got {type(path_str).__name__}")
        return issues

    if not path_str:
        issues.append(f"{label}: path must not be empty")
        return issues

    # Reject NUL, C0 controls, and DEL.
    for ch in path_str:
        cp = ord(ch)
        if cp == 0:
            issues.append(f"{label}: path contains NUL character")
            break
        if cp < 0x20:
            issues.append(f"{label}: path contains control character U+{cp:04X}")
            break
        if cp == 0x7F:
            issues.append(f"{label}: path contains DEL character U+007F")
            break

    # Reject backslash
    if "\\" in path_str:
        issues.append(f"{label}: path contains backslash: {path_str!r}")

    # Reject leading slash (absolute POSIX)
    if path_str.startswith("/"):
        issues.append(f"{label}: path must be relative (leading /): {path_str!r}")

    # Reject Windows drive/UNC absolute forms
    if re.match(r"^[A-Za-z]:", path_str):
        issues.append(f"{label}: path looks like Windows drive path: {path_str!r}")
    if path_str.startswith("\\\\") or path_str.startswith("//"):
        issues.append(f"{label}: path looks like UNC path: {path_str!r}")

    # Git repository paths cannot contain empty, dot, or dotdot segments.
    segments = path_str.split("/")
    if "" in segments:
        issues.append(f"{label}: path has empty segment: {path_str!r}")
    for seg in segments:
        if seg == ".":
            issues.append(f"{label}: path has dot segment: {path_str!r}")
            break
        if seg == "..":
            issues.append(f"{label}: path has dotdot segment: {path_str!r}")
            break

    return issues


# ── Strict type helpers ───────────────────────────────────────────────

def assert_int(val, label: str) -> list:
    """Return issues if val is not a strict int (bool is not int)."""
    issues = []
    if isinstance(val, bool) or not isinstance(val, int):
        issues.append(f"{label}: must be integer, got {type(val).__name__}({val!r})")
    return issues


def assert_str(val, label: str) -> list:
    """Return issues if val is not a string."""
    issues = []
    if not isinstance(val, str):
        issues.append(f"{label}: must be string, got {type(val).__name__}({val!r})")
    return issues


def assert_bool_true(val, label: str) -> list:
    """Return issues if val is not exactly True."""
    issues = []
    if val is not True:
        issues.append(f"{label}: must be boolean true, got {type(val).__name__}({val!r})")
    return issues


def assert_sha1(val, label: str) -> list:
    """Return issues if val is not exactly 40 lowercase hex."""
    issues = []
    if not isinstance(val, str) or not SHA1_RE.match(val):
        issues.append(f"{label}: must be 40 lowercase hex SHA, got {val!r}")
    return issues


def assert_sha256(val, label: str) -> list:
    """Return issues if val is not exactly 64 lowercase hex."""
    issues = []
    if not isinstance(val, str) or not SHA256_RE.match(val):
        issues.append(f"{label}: must be 64 lowercase hex SHA-256, got {val!r}")
    return issues


# ── Unknown-fields scanner ───────────────────────────────────────────

def unknown_fields(obj: dict, allowed: set, label: str) -> list:
    """Return issues for keys in obj that are not in allowed."""
    if not isinstance(obj, dict):
        return []
    extra = set(obj.keys()) - allowed
    if extra:
        return [f"{label}: unknown fields {sorted(extra)}"]
    return []


# ── Schema validation (inline) ───────────────────────────────────────

def validate_schema(inventory: dict) -> list:
    """Validate inventory against schema constraints (inline, no external dependency)."""
    issues = []
    root = "root"

    # Top-level unknown fields
    issues.extend(unknown_fields(inventory, TOP_LEVEL_KEYS, root))

    # Required top-level keys
    for key in sorted(TOP_LEVEL_KEYS):
        if key not in inventory:
            issues.append(f"{root}: missing required key '{key}'")

    # ── version ──
    if not isinstance(inventory.get("version"), int) or inventory.get("version") != 1:
        issues.append(f"{root}.version: must be integer 1, got {inventory.get('version')!r}")

    # ── ledger_id ──
    issues.extend(assert_str(inventory.get("ledger_id"), f"{root}.ledger_id"))
    lid = inventory.get("ledger_id", "")
    if isinstance(lid, str) and not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", lid):
        issues.append(f"{root}.ledger_id: must match pattern ^[a-z0-9][a-z0-9-]*[a-z0-9]$, got {lid!r}")

    # ── generated_at_branch ──
    issues.extend(assert_str(inventory.get("generated_at_branch"), f"{root}.generated_at_branch"))

    # ── branch_sha ──
    issues.extend(assert_sha1(inventory.get("branch_sha"), f"{root}.branch_sha"))

    # ── canonical_digest ──
    issues.extend(assert_sha256(inventory.get("canonical_digest"), f"{root}.canonical_digest"))

    # ── total_records ──
    issues.extend(assert_int(inventory.get("total_records"), f"{root}.total_records"))

    # ── policy ──
    policy = inventory.get("policy", {})
    if not isinstance(policy, dict):
        issues.append(f"{root}.policy: must be object")
    else:
        issues.extend(unknown_fields(policy, POLICY_KEYS, f"{root}.policy"))
        for bool_key in ["no_record_may_vanish", "no_record_may_duplicate",
                         "no_record_may_move_to_drop_without_user_approval"]:
            issues.extend(assert_bool_true(policy.get(bool_key), f"{root}.policy.{bool_key}"))
        issues.extend(assert_str(policy.get("drop_requires"), f"{root}.policy.drop_requires"))
        if isinstance(policy.get("drop_requires"), str) and not policy.get("drop_requires"):
            issues.append(f"{root}.policy.drop_requires: must be non-empty string")

    # ── source_prs ──
    source_prs = inventory.get("source_prs", {})
    if not isinstance(source_prs, dict):
        issues.append(f"{root}.source_prs: must be object")
    else:
        for pr_key, pr_val in source_prs.items():
            if not PR_KEY_RE.match(str(pr_key)):
                issues.append(f"{root}.source_prs: invalid PR key {pr_key!r}")
            if not isinstance(pr_val, dict):
                issues.append(f"{root}.source_prs.{pr_key}: must be object")
                continue
            issues.extend(unknown_fields(pr_val, PR_META_KEYS, f"{root}.source_prs.{pr_key}"))
            for field in sorted(PR_META_KEYS):
                if field not in pr_val:
                    issues.append(f"{root}.source_prs.{pr_key}: missing required field '{field}'")
            issues.extend(assert_sha1(pr_val.get("base_sha"), f"{root}.source_prs.{pr_key}.base_sha"))
            issues.extend(assert_sha1(pr_val.get("head_sha"), f"{root}.source_prs.{pr_key}.head_sha"))
            issues.extend(assert_int(pr_val.get("changed_path_count"), f"{root}.source_prs.{pr_key}.changed_path_count"))
            if "source_delta_sha256" in pr_val:
                issues.extend(assert_sha256(pr_val.get("source_delta_sha256"), f"{root}.source_prs.{pr_key}.source_delta_sha256"))

    # ── records ──
    records = inventory.get("records", [])
    if not isinstance(records, list):
        issues.append(f"{root}.records: must be array")
    else:
        for i, rec in enumerate(records):
            rp = f"{root}.records[{i}]"
            if not isinstance(rec, dict):
                issues.append(f"{rp}: must be object")
                continue

            # Unknown fields
            issues.extend(unknown_fields(rec, RECORD_KEYS, rp))

            # Required fields
            for field in ["source_pr", "path", "change_type", "subsystem",
                          "disposition", "classification_method", "base_sha", "head_sha"]:
                if field not in rec:
                    issues.append(f"{rp}: missing required field '{field}'")

            # source_pr
            issues.extend(assert_str(rec.get("source_pr"), f"{rp}.source_pr"))
            if isinstance(rec.get("source_pr"), str) and not PR_KEY_RE.match(rec["source_pr"]):
                issues.append(f"{rp}.source_pr: must match pr-NNNN pattern")

            # path
            issues.extend(validate_path(rec.get("path", ""), f"{rp}.path"))

            # change_type
            issues.extend(assert_str(rec.get("change_type"), f"{rp}.change_type"))
            if rec.get("change_type") not in VALID_CHANGE_TYPES:
                issues.append(f"{rp}.change_type: invalid value {rec.get('change_type')!r}")

            # subsystem
            issues.extend(assert_str(rec.get("subsystem"), f"{rp}.subsystem"))
            if rec.get("subsystem") not in VALID_SUBSYSTEMS:
                issues.append(f"{rp}.subsystem: invalid value {rec.get('subsystem')!r}")

            # disposition
            issues.extend(assert_str(rec.get("disposition"), f"{rp}.disposition"))
            if rec.get("disposition") not in VALID_DISPOSITIONS:
                issues.append(f"{rp}.disposition: invalid value {rec.get('disposition')!r}")

            # classification_method
            issues.extend(assert_str(rec.get("classification_method"), f"{rp}.classification_method"))
            if rec.get("classification_method") not in VALID_CLASSIFICATION_METHODS:
                issues.append(f"{rp}.classification_method: invalid value {rec.get('classification_method')!r}")

            # SHAs
            issues.extend(assert_sha1(rec.get("base_sha"), f"{rp}.base_sha"))
            issues.extend(assert_sha1(rec.get("head_sha"), f"{rp}.head_sha"))

            # rationale: must exist and be non-empty string
            if "rationale" not in rec:
                issues.append(f"{rp}: missing required field 'rationale'")
            elif not isinstance(rec["rationale"], str) or not rec["rationale"]:
                issues.append(f"{rp}.rationale: must be non-empty string")

            # user_approval_reference conditional
            disp = rec.get("disposition")
            if disp == "drop-blocked-user-approval":
                if "user_approval_reference" not in rec:
                    issues.append(f"{rp}: drop-blocked-user-approval requires 'user_approval_reference'")
                elif not isinstance(rec["user_approval_reference"], str) or not rec["user_approval_reference"]:
                    issues.append(f"{rp}.user_approval_reference: must be non-empty string for drop-blocked-user-approval")
            else:
                if "user_approval_reference" in rec and rec["user_approval_reference"] is not None:
                    issues.append(f"{rp}: non-drop record must not have user_approval_reference")

    # ── subsystem_totals ──
    st = inventory.get("subsystem_totals", {})
    if not isinstance(st, dict):
        issues.append(f"{root}.subsystem_totals: must be object")
    else:
        for k, v in st.items():
            issues.extend(assert_int(v, f"{root}.subsystem_totals.{k}"))

    # ── disposition_totals ──
    dt = inventory.get("disposition_totals", {})
    if not isinstance(dt, dict):
        issues.append(f"{root}.disposition_totals: must be object")
    else:
        for k, v in dt.items():
            issues.extend(assert_int(v, f"{root}.disposition_totals.{k}"))

    return issues


# ── Integrity checks ─────────────────────────────────────────────────

def validate_integrity(inventory: dict) -> list:
    """Validate integrity constraints: counts, digest, uniqueness, sort order, delta digests."""
    issues = []
    records = inventory.get("records")
    if not isinstance(records, list):
        return ["integrity checks require records to be an array"]
    source_prs = inventory.get("source_prs")
    if not isinstance(source_prs, dict):
        issues.append("integrity checks require source_prs to be an object")
        source_prs = {}

    # total_records must match actual count
    if inventory.get("total_records") != len(records):
        issues.append(
            f"total_records ({inventory.get('total_records')}) != actual count ({len(records)})"
        )

    # Canonical digest verification (64 hex SHA-256)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if inventory.get("canonical_digest") != expected_digest:
        issues.append(
            f"canonical_digest mismatch: expected {expected_digest}, "
            f"got {inventory.get('canonical_digest')}"
        )

    # Per-PR source_delta_sha256 validation
    pr_records = {}
    for index, rec in enumerate(records):
        if not isinstance(rec, dict):
            issues.append(f"records[{index}] cannot participate in integrity checks")
            continue
        sp = rec.get("source_pr")
        if not isinstance(sp, str):
            continue
        if sp not in pr_records:
            pr_records[sp] = []
        pr_records[sp].append(rec)

    for pr_key, pr_val in source_prs.items():
        if not isinstance(pr_val, dict):
            issues.append(f"source_prs.{pr_key} cannot participate in integrity checks")
            continue
        if "source_delta_sha256" not in pr_val:
            issues.append(f"source_prs.{pr_key}: missing required 'source_delta_sha256'")
            continue

        # Recompute: sorted (change_type<TAB>path) lines
        recs_for_pr = pr_records.get(pr_key, [])
        lines = []
        malformed_delta_record = False
        for record in recs_for_pr:
            change_type = record.get("change_type")
            path = record.get("path")
            if not isinstance(change_type, str) or not isinstance(path, str):
                malformed_delta_record = True
                continue
            lines.append(f"{change_type}\t{path}")
        if malformed_delta_record:
            issues.append(
                f"source_prs.{pr_key}: source delta cannot be computed from malformed records"
            )
            continue
        lines.sort()
        delta_text = "\n".join(lines) + "\n"
        expected_delta = hashlib.sha256(delta_text.encode("utf-8")).hexdigest()

        if pr_val["source_delta_sha256"] != expected_delta:
            issues.append(
                f"source_prs.{pr_key}.source_delta_sha256 mismatch: "
                f"expected {expected_delta}, got {pr_val['source_delta_sha256']}"
            )

    # No duplicate (source_pr, path) identities
    seen = set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        key = (rec.get("source_pr"), rec.get("path"))
        if not all(isinstance(value, str) for value in key):
            continue
        if key in seen:
            issues.append(f"Duplicate identity at records[{i}]: ({key[0]}, {key[1]})")
        seen.add(key)

    # Records must be sorted by (source_pr, path)
    for i in range(1, len(records)):
        if not isinstance(records[i - 1], dict) or not isinstance(records[i], dict):
            continue
        prev_key = (records[i - 1].get("source_pr", ""), records[i - 1].get("path", ""))
        curr_key = (records[i].get("source_pr", ""), records[i].get("path", ""))
        if not all(isinstance(value, str) for value in (*prev_key, *curr_key)):
            continue
        if prev_key > curr_key:
            issues.append(
                f"Records not sorted at index {i}: {prev_key} > {curr_key}"
            )
            break

    # Subsystem totals consistency
    actual_subsystem = Counter(
        record.get("subsystem")
        for record in records
        if isinstance(record, dict) and isinstance(record.get("subsystem"), str)
    )
    declared_subsystem = inventory.get("subsystem_totals", {})
    if dict(actual_subsystem) != declared_subsystem:
        issues.append("subsystem_totals inconsistent")

    # Disposition totals consistency
    actual_disposition = Counter(
        record.get("disposition")
        for record in records
        if isinstance(record, dict) and isinstance(record.get("disposition"), str)
    )
    declared_disposition = inventory.get("disposition_totals", {})
    if dict(actual_disposition) != declared_disposition:
        issues.append("disposition_totals inconsistent")

    # source_prs changed_path_count consistency
    actual_counts = Counter(
        record.get("source_pr")
        for record in records
        if isinstance(record, dict) and isinstance(record.get("source_pr"), str)
    )
    for pr_key, pr_val in source_prs.items():
        if not isinstance(pr_val, dict):
            continue
        declared = pr_val.get("changed_path_count", -1)
        actual = actual_counts.get(pr_key, 0)
        if declared != actual:
            issues.append(
                f"source_prs.{pr_key}.changed_path_count ({declared}) "
                f"!= actual record count ({actual})"
            )

    return issues


def validate_source_bindings(inventory: dict) -> list:
    """Bind the ledger to immutable source PR metadata and observed path sets."""
    issues = []
    source_prs = inventory.get("source_prs")
    records = inventory.get("records")
    if not isinstance(source_prs, dict) or not isinstance(records, list):
        return ["immutable source binding cannot be evaluated"]

    expected_keys = set(EXPECTED_SOURCE_PRS)
    actual_keys = set(source_prs)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    if missing:
        issues.append(f"immutable source mapping missing PRs: {missing}")
    if extra:
        issues.append(f"immutable source mapping has unexpected PRs: {extra}")

    grouped = {pr_key: [] for pr_key in EXPECTED_SOURCE_PRS}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            issues.append(f"records[{index}] prevents immutable source binding")
            continue
        pr_key = record.get("source_pr")
        if not isinstance(pr_key, str) or pr_key not in EXPECTED_SOURCE_PRS:
            issues.append(f"records[{index}] references unexpected source PR {pr_key!r}")
            continue
        expected = EXPECTED_SOURCE_PRS[pr_key]
        for sha_field in ("base_sha", "head_sha"):
            if record.get(sha_field) != expected[sha_field]:
                issues.append(
                    f"records[{index}].{sha_field} does not match immutable {pr_key} binding"
                )
        grouped[pr_key].append(record)

    for pr_key, expected in EXPECTED_SOURCE_PRS.items():
        metadata = source_prs.get(pr_key)
        if not isinstance(metadata, dict):
            continue
        for field, expected_value in expected.items():
            if metadata.get(field) != expected_value:
                issues.append(
                    f"source_prs.{pr_key}.{field} does not match immutable expected value"
                )

        pr_records = grouped[pr_key]
        expected_count = expected.get("changed_path_count")
        if not isinstance(expected_count, int) or isinstance(expected_count, bool):
            issues.append(f"{pr_key} immutable expected path count is unavailable")
            continue
        if len(pr_records) != expected_count:
            issues.append(
                f"{pr_key} observed record count {len(pr_records)} does not match "
                f"immutable expected {expected_count}"
            )
        try:
            delta_lines = sorted(
                f"{record['change_type']}\t{record['path']}" for record in pr_records
            )
        except (KeyError, TypeError):
            issues.append(f"{pr_key} source delta cannot be computed from malformed records")
            continue
        delta_text = "\n".join(delta_lines) + "\n"
        observed_delta = hashlib.sha256(delta_text.encode("utf-8")).hexdigest()
        expected_delta = expected.get("source_delta_sha256")
        if not isinstance(expected_delta, str):
            issues.append(f"{pr_key} immutable expected source delta is unavailable")
            continue
        if observed_delta != expected_delta:
            issues.append(
                f"{pr_key} observed source delta does not match immutable expected digest"
            )

    return issues


# ── External schema validation ────────────────────────────────────────

def validate_json_schema(inventory: dict, schema_path: str) -> list:
    """Validate against external draft-07 schema, failing closed if unavailable."""
    issues = []
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return ["schema validation requested but jsonschema is not installed"]

    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        return [f"Cannot load schema {schema_path}: {e}"]

    import jsonschema
    try:
        jsonschema.Draft7Validator.check_schema(schema)
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(inventory))
    except jsonschema.SchemaError as error:
        return [f"Invalid draft-07 schema: {error.message}"]
    except Exception as error:
        return [f"Draft-07 schema validation could not complete: {error}"]
    for err in errors:
        path = ".".join(str(p) for p in err.absolute_path) or "root"
        issues.append(f"schema:{path}: {err.message}")

    return issues


# ── Main entry point ─────────────────────────────────────────────────

def check(inventory_path: str, schema_path: str = None) -> int:
    """Run all checks. Return 0 on pass, 1 on fail."""
    try:
        inventory = load_inventory(inventory_path)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"FATAL: Cannot load inventory: {e}", file=sys.stderr)
        return 1

    if not isinstance(inventory, dict):
        print("INVENTORY CHECK FAILED", file=sys.stderr)
        print("  - root: inventory must be an object", file=sys.stderr)
        return 1

    all_issues = []
    all_issues.extend(validate_schema(inventory))
    all_issues.extend(validate_integrity(inventory))
    all_issues.extend(validate_source_bindings(inventory))

    # External schema validation
    if schema_path:
        schema_issues = validate_json_schema(inventory, schema_path)
        all_issues.extend(schema_issues)

    if all_issues:
        print("INVENTORY CHECK FAILED", file=sys.stderr)
        for issue in all_issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print("INVENTORY CHECK PASSED")
    print(f"  Records: {inventory['total_records']}")
    print(f"  Digest: {inventory['canonical_digest']}")
    print(f"  Source PRs: {list(inventory['source_prs'].keys())}")
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check Portal-on-Hermes inventory")
    parser.add_argument("inventory", help="Path to inventory.json")
    parser.add_argument("--schema", help="Path to JSON Schema file for external validation")
    args = parser.parse_args()

    sys.exit(check(args.inventory, args.schema))


if __name__ == "__main__":
    main()
