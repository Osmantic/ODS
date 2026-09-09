#!/usr/bin/env python3
"""
Tests for Portal-on-Hermes predecessor preservation inventory checker.

Run with: python3 -m unittest -v test_check_inventory

Tests cover:
  - Valid inventory passes all checks
  - Type violations (float instead of int, string instead of bool, bool as int)
  - Schema violations (missing required fields, unknown properties, every level)
  - Path traversal, absolute paths, backslash, NUL/control, dot/dotdot, drive/UNC
  - Duplicate identity rejection
  - Invalid SHA detection (40-char for SHAs, 64-char for digest)
  - Count/digest mismatches
  - Unauthorized drop (missing user_approval_reference)
  - Non-drop with approval reference (must be rejected)
  - Rationale required / non-empty
  - Sort order violations
  - Subsystem/disposition total inconsistencies
  - source_delta_sha256 per-PR validation
  - source_pr metadata unknown fields
  - policy unknown fields
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_inventory as checker
from check_inventory import (
    check,
    validate_json_schema,
    validate_schema,
    validate_integrity,
    validate_path,
)
from generate_inventory import classify_disposition, classify_subsystem


HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTION_INVENTORY = os.path.join(HERE, "inventory.json")
PRODUCTION_SCHEMA = os.path.join(HERE, "inventory.schema.json")


def sha1_hex(n=40):
    """Return a valid 40-char lowercase hex string."""
    return "a" * n


def sha256_hex():
    """Return a valid 64-char lowercase hex string."""
    return "b" * 64


def make_valid_inventory():
    """Create a minimal valid inventory for testing."""
    records = [
        {
            "source_pr": "pr-100",
            "path": "foo/bar.py",
            "change_type": "A",
            "subsystem": "core-runtime",
            "disposition": "retain-semantics",
            "classification_method": "mechanical",
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "rationale": "test record one",
        },
        {
            "source_pr": "pr-100",
            "path": "foo/baz.py",
            "change_type": "M",
            "subsystem": "tests-docs",
            "disposition": "orthogonal-retain",
            "classification_method": "mechanical",
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "rationale": "test record two",
        },
    ]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # Compute per-PR delta
    lines = []
    for r in records:
        lines.append(f"{r['change_type']}\t{r['path']}")
    lines.sort()
    delta_text = "\n".join(lines) + "\n"
    delta_digest = hashlib.sha256(delta_text.encode("utf-8")).hexdigest()

    return {
        "version": 1,
        "ledger_id": "test-ledger-01",
        "generated_at_branch": "test-branch",
        "branch_sha": "c" * 40,
        "source_prs": {
            "pr-100": {
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "changed_path_count": 2,
                "source_delta_sha256": delta_digest,
            }
        },
        "total_records": 2,
        "canonical_digest": digest,
        "records": records,
        "subsystem_totals": {"core-runtime": 1, "tests-docs": 1},
        "disposition_totals": {"retain-semantics": 1, "orthogonal-retain": 1},
        "policy": {
            "no_record_may_vanish": True,
            "no_record_may_duplicate": True,
            "no_record_may_move_to_drop_without_user_approval": True,
            "drop_requires": "explicit user-approval reference",
        },
    }


def write_and_check(inv: dict, schema_path=None, enforce_source_binding=False):
    """Write inventory and run check, optionally using immutable production bindings."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(inv, f)
        path = f.name
    original_bindings = checker.EXPECTED_SOURCE_PRS
    if not enforce_source_binding:
        checker.EXPECTED_SOURCE_PRS = {
            pr_key: deepcopy(metadata)
            for pr_key, metadata in inv.get("source_prs", {}).items()
            if isinstance(metadata, dict)
        }
    try:
        return check(path, schema_path)
    finally:
        checker.EXPECTED_SOURCE_PRS = original_bindings
        os.unlink(path)


# ── Test classes ──────────────────────────────────────────────────────

class TestValidInventory(unittest.TestCase):
    """A correct inventory passes all checks."""

    def test_valid_inventory_passes(self):
        inv = make_valid_inventory()
        rc = write_and_check(inv)
        self.assertEqual(rc, 0, "Valid inventory should pass")

    def test_valid_inventory_no_schema_issues(self):
        inv = make_valid_inventory()
        issues = validate_schema(inv)
        self.assertEqual(issues, [], f"Expected no schema issues, got: {issues}")

    def test_valid_inventory_no_integrity_issues(self):
        inv = make_valid_inventory()
        issues = validate_integrity(inv)
        self.assertEqual(issues, [], f"Expected no integrity issues, got: {issues}")


class TestTypeViolations(unittest.TestCase):
    """Strict bool/int/float type enforcement."""

    def test_float_total_records_rejected(self):
        inv = make_valid_inventory()
        inv["total_records"] = 2.0
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Float total_records must be rejected")

    def test_string_policy_bool_rejected(self):
        inv = make_valid_inventory()
        inv["policy"]["no_record_may_vanish"] = "true"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "String policy bool must be rejected")

    def test_float_version_rejected(self):
        inv = make_valid_inventory()
        inv["version"] = 1.0
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Float version must be rejected")

    def test_bool_as_int_rejected_for_total_records(self):
        inv = make_valid_inventory()
        inv["total_records"] = True
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Bool as int for total_records must be rejected")

    def test_bool_as_int_rejected_for_changed_path_count(self):
        inv = make_valid_inventory()
        inv["source_prs"]["pr-100"]["changed_path_count"] = True
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Bool as int for changed_path_count must be rejected")

    def test_bool_as_int_rejected_for_subsystem_totals(self):
        inv = make_valid_inventory()
        inv["subsystem_totals"]["core-runtime"] = True
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Bool as int for subsystem_totals must be rejected")

    def test_bool_as_int_rejected_for_disposition_totals(self):
        inv = make_valid_inventory()
        inv["disposition_totals"]["retain-semantics"] = True
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0, "Bool as int for disposition_totals must be rejected")


class TestSchemaViolations(unittest.TestCase):
    """Missing fields, unknown properties, invalid values."""

    def test_missing_canonical_digest_rejected(self):
        inv = make_valid_inventory()
        del inv["canonical_digest"]
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_unknown_top_level_property_rejected(self):
        inv = make_valid_inventory()
        inv["extra_field"] = "oops"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_unknown_record_field_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["extra"] = "value"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_missing_record_required_field_rejected(self):
        inv = make_valid_inventory()
        del inv["records"][0]["source_pr"]
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_subsystem_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["subsystem"] = "nonexistent-subsystem"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_disposition_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["disposition"] = "casual-drop"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_change_type_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["change_type"] = "X"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_classification_method_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["classification_method"] = "magic"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_policy_unknown_field_rejected(self):
        inv = make_valid_inventory()
        inv["policy"]["extra_policy_key"] = "bad"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_source_pr_metadata_unknown_field_rejected(self):
        inv = make_valid_inventory()
        inv["source_prs"]["pr-100"]["extra_meta"] = "bad"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_pr_key_rejected(self):
        inv = make_valid_inventory()
        inv["source_prs"]["invalid-key"] = inv["source_prs"].pop("pr-100")
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_policy_bool_false_rejected(self):
        inv = make_valid_inventory()
        inv["policy"]["no_record_may_duplicate"] = False
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)


class TestPathValidation(unittest.TestCase):
    """Path must be POSIX repository-relative UTF-8 only."""

    def test_path_traversal_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "../etc/passwd"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_absolute_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "/usr/bin/python"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_empty_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = ""
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_backslash_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "foo\\bar.py"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_nul_in_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "foo\x00bar.py"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_control_char_in_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "foo\x01bar.py"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_dot_segment_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "foo/./bar.py"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_dotdot_segment_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "foo/../bar.py"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_windows_drive_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "C:\\Users\\foo"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_unc_path_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "\\\\server\\share"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_benign_double_dot_filename_accepted(self):
        """Two dots inside a segment (e.g. file.tar.gz) are OK."""
        inv = make_valid_inventory()
        inv["records"][0]["path"] = "data/file.tar.gz"
        # Recompute canonical digest and delta for the modified record
        _recompute_digests(inv)
        rc = write_and_check(inv)
        self.assertEqual(rc, 0, "Benign filename with two dots must pass")

    def test_valid_deep_path_accepted(self):
        """Deep nested paths with normal segments are fine."""
        inv = make_valid_inventory()
        # Replace both records with same PR to keep sort order valid
        inv["records"][0]["path"] = "a/first.py"
        inv["records"][1]["path"] = "ods/extensions/services/foo/bar/baz.py"
        # Recompute canonical digest and delta for the modified records
        _recompute_digests(inv)
        rc = write_and_check(inv)
        self.assertEqual(rc, 0, "Deep nested path must pass")


class TestSHAValidation(unittest.TestCase):
    """SHA fields must be exact format."""

    def test_invalid_sha_too_short(self):
        inv = make_valid_inventory()
        inv["records"][0]["base_sha"] = "abc123"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_sha_uppercase(self):
        inv = make_valid_inventory()
        inv["records"][0]["base_sha"] = "A" * 40
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_invalid_sha_wrong_length_64(self):
        """A 64-char string in a 40-char SHA field must fail."""
        inv = make_valid_inventory()
        inv["records"][0]["base_sha"] = "a" * 64
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_branch_sha_invalid(self):
        inv = make_valid_inventory()
        inv["branch_sha"] = "short"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_canonical_digest_wrong_length_40(self):
        """canonical_digest must be 64 hex; 40 hex must fail."""
        inv = make_valid_inventory()
        inv["canonical_digest"] = "a" * 40
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_canonical_digest_uppercase_rejected(self):
        inv = make_valid_inventory()
        inv["canonical_digest"] = "B" * 64
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)


class TestIntegrityViolations(unittest.TestCase):
    """Digest, count, sort, uniqueness, delta."""

    def test_count_mismatch_rejected(self):
        inv = make_valid_inventory()
        inv["total_records"] = 5
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_digest_mismatch_rejected(self):
        inv = make_valid_inventory()
        inv["canonical_digest"] = "dead" * 16
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_duplicate_identity_rejected(self):
        inv = make_valid_inventory()
        inv["records"].append(inv["records"][0].copy())
        inv["total_records"] = 3
        # Recompute digest for the new record list
        canonical = json.dumps(inv["records"], sort_keys=True, separators=(",", ":"))
        inv["canonical_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        inv["source_prs"]["pr-100"]["changed_path_count"] = 3
        # Recompute delta
        lines = [f"{r['change_type']}\t{r['path']}" for r in inv["records"]]
        lines.sort()
        delta_text = "\n".join(lines) + "\n"
        inv["source_prs"]["pr-100"]["source_delta_sha256"] = hashlib.sha256(
            delta_text.encode("utf-8")
        ).hexdigest()
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_sort_order_violation_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0], inv["records"][1] = inv["records"][1], inv["records"][0]
        canonical = json.dumps(inv["records"], sort_keys=True, separators=(",", ":"))
        inv["canonical_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_subsystem_totals_inconsistent_rejected(self):
        inv = make_valid_inventory()
        inv["subsystem_totals"] = {"core-runtime": 99}
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_disposition_totals_inconsistent_rejected(self):
        inv = make_valid_inventory()
        inv["disposition_totals"] = {"retain-semantics": 99}
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_source_pr_count_mismatch_rejected(self):
        inv = make_valid_inventory()
        inv["source_prs"]["pr-100"]["changed_path_count"] = 100
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_missing_source_delta_sha256_rejected(self):
        inv = make_valid_inventory()
        del inv["source_prs"]["pr-100"]["source_delta_sha256"]
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_wrong_source_delta_sha256_rejected(self):
        inv = make_valid_inventory()
        inv["source_prs"]["pr-100"]["source_delta_sha256"] = "ff" * 32
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_non_object_record_rejected_without_crash(self):
        inv = make_valid_inventory()
        inv["records"][0] = []
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_non_string_source_pr_rejected_without_crash(self):
        inv = make_valid_inventory()
        inv["records"][0]["source_pr"] = []
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_non_string_path_rejected_without_crash(self):
        inv = make_valid_inventory()
        inv["records"][0]["path"] = []
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_top_level_array_rejected_without_crash(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as target:
            json.dump([], target)
            path = target.name
        try:
            rc = check(path)
        finally:
            os.unlink(path)
        self.assertNotEqual(rc, 0)


class TestRationaleAndApproval(unittest.TestCase):
    """Rationale required; approval conditional on disposition."""

    def test_missing_rationale_rejected(self):
        inv = make_valid_inventory()
        del inv["records"][0]["rationale"]
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_empty_rationale_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["rationale"] = ""
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_drop_without_approval_reference_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["disposition"] = "drop-blocked-user-approval"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_drop_with_empty_approval_reference_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["disposition"] = "drop-blocked-user-approval"
        inv["records"][0]["user_approval_reference"] = ""
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)

    def test_drop_with_valid_approval_reference_passes(self):
        inv = make_valid_inventory()
        inv["records"][0]["disposition"] = "drop-blocked-user-approval"
        inv["records"][0]["user_approval_reference"] = "PR-9999 approved by @owner"
        # Update disposition totals
        inv["disposition_totals"] = {"drop-blocked-user-approval": 1, "orthogonal-retain": 1}
        # Recompute canonical digest and delta
        _recompute_digests(inv)
        rc = write_and_check(inv)
        self.assertEqual(rc, 0, "Drop with valid approval reference must pass")

    def test_non_drop_with_approval_reference_rejected(self):
        inv = make_valid_inventory()
        inv["records"][0]["user_approval_reference"] = "should not be here"
        rc = write_and_check(inv)
        self.assertNotEqual(rc, 0)


def _recompute_digests(inv):
    """Recompute canonical_digest and all source_delta_sha256 after mutation."""
    records = inv["records"]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    inv["canonical_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    pr_records = {}
    for r in records:
        sp = r["source_pr"]
        if sp not in pr_records:
            pr_records[sp] = []
        pr_records[sp].append(r)
    for pr_key, pr_val in inv["source_prs"].items():
        recs = pr_records.get(pr_key, [])
        lines = [f"{r['change_type']}\t{r['path']}" for r in recs]
        lines.sort()
        delta_text = "\n".join(lines) + "\n"
        pr_val["source_delta_sha256"] = hashlib.sha256(
            delta_text.encode("utf-8")
        ).hexdigest()


class TestPathValidatorDirect(unittest.TestCase):
    """Direct tests on validate_path helper."""

    def test_valid_path(self):
        issues = validate_path("ods/bin/foo.py", "test")
        self.assertEqual(issues, [])

    def test_empty(self):
        issues = validate_path("", "test")
        self.assertTrue(any("empty" in i.lower() for i in issues))

    def test_nul(self):
        issues = validate_path("foo\x00bar", "test")
        self.assertTrue(any("nul" in i.lower() for i in issues))

    def test_control(self):
        issues = validate_path("foo\x1fbar", "test")
        self.assertTrue(any("control" in i.lower() for i in issues))

    def test_backslash(self):
        issues = validate_path("foo\\bar", "test")
        self.assertTrue(any("backslash" in i.lower() for i in issues))

    def test_leading_slash(self):
        issues = validate_path("/foo/bar", "test")
        self.assertTrue(any("leading" in i.lower() or "relative" in i.lower() for i in issues))

    def test_dotdot(self):
        issues = validate_path("foo/../bar", "test")
        self.assertTrue(any("dotdot" in i.lower() for i in issues))

    def test_dot_segment(self):
        issues = validate_path("foo/./bar", "test")
        self.assertTrue(any("dot segment" in i.lower() for i in issues))

    def test_benign_double_dot(self):
        issues = validate_path("data/file.tar.gz", "test")
        self.assertEqual(issues, [])

    def test_drive_path(self):
        issues = validate_path("C:\\Users\\foo", "test")
        self.assertTrue(len(issues) > 0)

    def test_empty_segment(self):
        issues = validate_path("foo//bar", "test")
        self.assertTrue(any("empty segment" in issue.lower() for issue in issues))

    def test_trailing_slash(self):
        issues = validate_path("foo/bar/", "test")
        self.assertTrue(any("empty segment" in issue.lower() for issue in issues))

    def test_delete_control_character(self):
        issues = validate_path("foo/\x7fbar", "test")
        self.assertTrue(any("del character" in issue.lower() for issue in issues))


class TestMechanicalClassification(unittest.TestCase):
    """Representative predecessor paths keep evidence-backed capability routes."""

    CASES = (
        ("ods/extensions/services/pixel-agent/host/workspace_preview.py", "previews", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/host/pixel-artifact-promoter.service", "previews", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/host/extension_manager.py", "previews", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/tests/test_workspace_preview.py", "tests-docs", "retain-semantics"),
        ("ods/extensions/services/dashboard-api/tests/test_pixel_preview_proxy.py", "tests-docs", "retain-semantics"),
        ("ods/bin/pixel_provider/scopes.py", "access-scopes", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/host/access_mode_server.py", "access-scopes", "replace-openclaw-coupling"),
        ("ods/extensions/services/dashboard-api/routers/pixel_scopes.py", "access-scopes", "replace-openclaw-coupling"),
        ("ods/bin/pixel_access_bridge.py", "access-scopes", "review-required"),
        ("ods/bin/ods-pixel-approve", "leases-approvals", "replace-openclaw-coupling"),
        ("ods/bin/ods-pixel-route-lease", "leases-approvals", "replace-openclaw-coupling"),
        ("ods/bin/pixel_provider/handoff_approvals.py", "leases-approvals", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/plugin/provider-lease-worker.mjs", "leases-approvals", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/tests/handoff_owner_worker.test.mjs", "tests-docs", "retain-semantics"),
        ("ods/extensions/services/pixel-edge/edge_entrypoint.py", "ingress-edge", "retain-semantics"),
        ("ods/extensions/services/pixel-agent/host/pixel_ingress.mjs", "ingress-edge", "replace-openclaw-coupling"),
        ("ods/extensions/services/pixel-agent/host/system_observe.py", "observability", "replace-openclaw-coupling"),
        ("ods/extensions/services/litellm/ods_token_spy_callback.py", "observability", "retain-semantics"),
        ("ods/bin/source_broker/calendar.py", "source-broker", "review-required"),
        ("somedir/Makefile", "other", "review-required"),
    )

    def test_routes_and_dispositions(self):
        for path, expected_subsystem, expected_disposition in self.CASES:
            with self.subTest(path=path):
                subsystem = classify_subsystem(path)
                disposition, method, rationale = classify_disposition(path, subsystem, "A")
                self.assertEqual(subsystem, expected_subsystem)
                self.assertEqual(disposition, expected_disposition)
                self.assertEqual(method, "mechanical")
                self.assertTrue(rationale)


class TestProductionSourceBindings(unittest.TestCase):
    """The checked-in ledger is bound to the exact two predecessor diffs."""

    @staticmethod
    def load_inventory():
        with open(PRODUCTION_INVENTORY, encoding="utf-8") as source:
            return json.load(source)

    def test_checked_in_inventory_passes_immutable_bindings(self):
        self.assertEqual(
            write_and_check(self.load_inventory(), enforce_source_binding=True),
            0,
        )

    def test_unexpected_source_pr_rejected(self):
        inventory = self.load_inventory()
        inventory["source_prs"]["pr-9999"] = deepcopy(
            inventory["source_prs"]["pr-3385"]
        )
        self.assertNotEqual(write_and_check(inventory, enforce_source_binding=True), 0)

    def test_source_metadata_substitution_rejected(self):
        inventory = self.load_inventory()
        inventory["source_prs"]["pr-3385"]["head_sha"] = "0" * 40
        self.assertNotEqual(write_and_check(inventory, enforce_source_binding=True), 0)

    def test_record_sha_substitution_rejected_after_self_digest_repair(self):
        inventory = self.load_inventory()
        inventory["records"][0]["base_sha"] = "0" * 40
        _recompute_digests(inventory)
        self.assertNotEqual(write_and_check(inventory, enforce_source_binding=True), 0)

    def test_path_substitution_rejected_after_self_digest_repair(self):
        inventory = self.load_inventory()
        inventory["records"][0]["path"] = "ods/.fabricated-preservation-entry"
        inventory["records"].sort(key=lambda item: (item["source_pr"], item["path"]))
        _recompute_digests(inventory)
        self.assertNotEqual(write_and_check(inventory, enforce_source_binding=True), 0)

    def test_missing_jsonschema_fails_closed(self):
        with mock.patch.dict(sys.modules, {"jsonschema": None}):
            issues = validate_json_schema(self.load_inventory(), PRODUCTION_SCHEMA)
        self.assertTrue(any("not installed" in issue for issue in issues))

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema"),
        "jsonschema is not installed in this interpreter",
    )
    def test_unresolvable_schema_reference_fails_closed(self):
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "#/definitions/missing",
            "definitions": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as target:
            json.dump(schema, target)
            schema_path = target.name
        try:
            issues = validate_json_schema(self.load_inventory(), schema_path)
        finally:
            os.unlink(schema_path)
        self.assertTrue(any("could not complete" in issue for issue in issues))

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema"),
        "jsonschema is not installed in this interpreter",
    )
    def test_checked_in_inventory_passes_draft7_schema(self):
        self.assertEqual(
            validate_json_schema(self.load_inventory(), PRODUCTION_SCHEMA),
            [],
        )


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
